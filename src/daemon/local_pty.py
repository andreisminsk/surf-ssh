"""Platform-specific local PTY management for the Local Console feature."""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ShellInfo:
    """Describes an available local shell."""
    id: str
    name: str
    command: list[str]


def _find_in_path(name: str) -> str | None:
    """Return full path to an executable if found in PATH."""
    return shutil.which(name)


def discover_shells() -> list[ShellInfo]:
    """Discover available shells on the current platform.

    On Windows: PowerShell, CMD, Git Bash, WSL.
    On Unix: bash, zsh, sh.
    """
    shells: list[ShellInfo] = []
    system = platform.system()

    if system == "Windows":
        # PowerShell (Windows Terminal / pwsh or built-in)
        pwsh = _find_in_path("pwsh")
        if pwsh:
            shells.append(ShellInfo("powershell", "PowerShell 7+", [pwsh, "-NoLogo"]))
        powershell = _find_in_path("powershell")
        if powershell:
            shells.append(ShellInfo("powershell", "PowerShell", [powershell, "-NoLogo"]))

        # CMD
        cmd = _find_in_path("cmd")
        if cmd:
            shells.append(ShellInfo("cmd", "Command Prompt", [cmd]))

        # Git Bash
        git_bash = _find_in_path("bash")
        if git_bash and "git" in git_bash.lower():
            shells.append(ShellInfo("git-bash", "Git Bash", [git_bash, "--login", "-i"]))

        # WSL
        wsl = _find_in_path("wsl")
        if wsl:
            shells.append(ShellInfo("wsl", "WSL (Ubuntu)", [wsl]))
    else:
        # Unix-like
        for name, display in [("bash", "Bash"), ("zsh", "Zsh"), ("sh", "SH")]:
            path = _find_in_path(name)
            if path:
                shells.append(ShellInfo(name, display, [path, "-l"]))

    if not shells:
        # Fallback to system default
        default_shell = os.environ.get("SHELL", "/bin/sh") if system != "Windows" else "cmd"
        shells.append(ShellInfo("default", "Default", [default_shell]))

    return shells


def get_shell_by_id(shell_id: str) -> ShellInfo | None:
    """Find a discovered shell by its ID. Returns None if not found."""
    for shell in discover_shells():
        if shell.id == shell_id:
            return shell
    return None


class LocalPtyManager:
    """Wraps a local PTY process for WebSocket relay.

    Uses pywinpty on Windows and the standard pty module on Unix.
    """

    def __init__(self, shell_command: list[str], cwd: str | None = None) -> None:
        self._shell_command = shell_command
        self._cwd = cwd or os.path.expanduser("~")
        self._output_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._read_task: asyncio.Task[None] | None = None
        self._exit_code: int | None = None
        self._pty: Any = None
        self._closed = False

    async def start(self, cols: int = 80, rows: int = 24) -> None:
        """Start the local PTY process."""
        system = platform.system()

        if system == "Windows":
            await self._start_winpty(cols, rows)
        else:
            await self._start_unix_pty(cols, rows)

        self._read_task = asyncio.create_task(self._read_loop())

    async def _start_winpty(self, cols: int, rows: int) -> None:
        """Start PTY using pywinpty on Windows."""
        from winpty import PTY

        self._pty = PTY(cols, rows)
        # pywinpty spawn: spawn(appname, cmdline=None, cwd=None, env=None)
        exe = self._shell_command[0]
        cmdline = " ".join(self._shell_command[1:]) if len(self._shell_command) > 1 else None
        self._pty.spawn(exe, cmdline=cmdline, cwd=self._cwd)

    async def _start_unix_pty(self, cols: int, rows: int) -> None:
        """Start PTY using the Unix pty module."""
        import pty
        import struct
        import fcntl
        import termios
        import signal

        master_fd, slave_fd = pty.openpty()

        # Set terminal size
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

        pid = os.fork()
        if pid == 0:
            # Child process
            os.close(master_fd)
            os.setsid()
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            if slave_fd > 2:
                os.close(slave_fd)

            # Set environment for proper terminal
            os.environ["TERM"] = "xterm-256color"
            os.chdir(self._cwd)

            os.execvp(self._shell_command[0], self._shell_command)
            os._exit(127)

        # Parent process
        os.close(slave_fd)
        self._pty = {"master_fd": master_fd, "pid": pid}

    async def _read_loop(self) -> None:
        """Continuously read PTY output into the queue."""
        system = platform.system()

        try:
            if system == "Windows":
                loop = asyncio.get_event_loop()
                while not self._closed:
                    # Non-blocking read so the executor thread can exit when
                    # _closed is set (blocking reads prevent process exit).
                    data = await loop.run_in_executor(None, self._pty.read, False)
                    if data:
                        if isinstance(data, str):
                            data = data.encode("utf-8", errors="replace")
                        await self._output_queue.put(data)
                    else:
                        # No data — check if process exited, then brief sleep
                        if not self._pty.isalive():
                            break
                        await asyncio.sleep(0.02)
            else:
                # Unix: read from master_fd
                loop = asyncio.get_event_loop()
                master_fd = self._pty["master_fd"]
                while not self._closed:
                    data = await loop.run_in_executor(None, os.read, master_fd, 4096)
                    if data:
                        await self._output_queue.put(data)
                    else:
                        break

                # Wait for child to exit — use non-blocking WNOHANG to avoid
                # blocking the executor thread during shutdown. If the child
                # hasn't exited yet (e.g., shell waiting for input), we skip
                # the wait; the close() method will kill it explicitly.
                pid = self._pty["pid"]
                try:
                    child_pid, status = os.waitpid(pid, os.WNOHANG)
                    if child_pid:
                        self._exit_code = os.waitstatus_to_exitcode(status)
                except ChildProcessError:
                    pass  # Already reaped

        except Exception as e:
            logger.error("Local PTY read error: %s", e)
        finally:
            await self._output_queue.put(b"")

    async def read(self) -> str:
        """Read next chunk of output. Returns empty string when session ends."""
        chunk = await self._output_queue.get()
        if not chunk:
            return ""
        return chunk.decode("utf-8", errors="replace")

    async def write(self, data: str) -> None:
        """Write input to the PTY."""
        if self._closed:
            return
        system = platform.system()
        if system == "Windows":
            # pywinpty write expects str, not bytes
            self._pty.write(data)
        else:
            os.write(self._pty["master_fd"], data.encode("utf-8", errors="replace"))

    async def resize(self, cols: int, rows: int) -> None:
        """Resize the PTY."""
        if self._closed:
            return
        system = platform.system()

        if system == "Windows":
            self._pty.set_size(cols, rows)
        else:
            import struct
            import fcntl
            import termios

            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self._pty["master_fd"], termios.TIOCSWINSZ, winsize)

    def get_exit_code(self) -> int | None:
        """Return exit code if the process has exited."""
        if self._exit_code is not None:
            return self._exit_code
        if platform.system() == "Windows" and self._pty and not self._pty.isalive():
            return self._pty.get_exitstatus() or 0
        return None

    async def close(self) -> None:
        """Close the PTY session and kill the process."""
        if self._closed:
            return
        self._closed = True

        # Close the PTY FIRST — this unblocks the blocking read in the
        # executor thread, allowing the read task to finish.
        if self._pty:
            system = platform.system()
            if system == "Windows":
                try:
                    self._pty.close()
                except Exception:
                    pass
            else:
                try:
                    os.close(self._pty["master_fd"])
                except Exception:
                    pass
                # Kill child if still running
                try:
                    os.kill(self._pty["pid"], signal.SIGTERM)
                except Exception:
                    pass

        # Now cancel the read task — it should unblock once the PTY is closed
        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
            try:
                await asyncio.wait_for(self._read_task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        self._pty = None


class LocalPtyRegistry:
    """Tracks active local PTY sessions for cleanup on shutdown."""

    def __init__(self, max_sessions: int = 3) -> None:
        self._sessions: set[LocalPtyManager] = set()
        self._max_sessions = max_sessions

    def acquire_slot(self) -> bool:
        """Try to acquire a local terminal slot. Returns True if available."""
        if len(self._sessions) >= self._max_sessions:
            return False
        return True

    def register(self, manager: LocalPtyManager) -> None:
        """Register an active PTY session."""
        self._sessions.add(manager)

    def unregister(self, manager: LocalPtyManager) -> None:
        """Unregister a PTY session."""
        self._sessions.discard(manager)

    async def close_all(self) -> None:
        """Close all active local PTY sessions on shutdown."""
        for manager in list(self._sessions):
            try:
                await manager.close()
            except Exception:
                pass
        self._sessions.clear()
