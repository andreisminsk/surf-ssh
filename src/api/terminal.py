"""WebSocket terminal endpoint with PTY relay."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

import asyncssh
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from src.ssh.connection_pool import ConnectionPool

logger = logging.getLogger(__name__)
router = APIRouter()


def get_pool() -> ConnectionPool:
    raise NotImplementedError("Pool not configured")


class TerminalManager:
    """Wraps an AsyncSSH PTY session for WebSocket relay."""

    def __init__(
        self,
        conn: asyncssh.SSHClientConnection,
        term_type: str = "xterm-256color",
    ) -> None:
        self._conn = conn
        self._term_type = term_type
        self._process: asyncssh.SSHClientProcess | None = None
        self._output_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._read_task: asyncio.Task[None] | None = None
        self._exit_code: int | None = None

    async def start(self, cols: int = 80, rows: int = 24) -> None:
        """Start the PTY session."""
        self._process = await self._conn.create_process(
            term_type=self._term_type,
            term_size=(cols, rows),
            stdin=asyncssh.PIPE,
            stdout=asyncssh.PIPE,
            stderr=asyncssh.STDOUT,
        )
        self._read_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        """Continuously read PTY output into the queue."""
        assert self._process is not None
        try:
            while not self._process.stdout.at_eof():
                chunk = await self._process.stdout.read(4096)
                if chunk:
                    if isinstance(chunk, str):
                        chunk = chunk.encode("utf-8")
                    await self._output_queue.put(chunk)
        except asyncssh.DisconnectError:
            pass
        except Exception as e:
            logger.error("Terminal read error: %s", e)
        finally:
            # Signal end of output
            await self._output_queue.put(b"")

    async def read(self) -> str:
        """Read next chunk of output. Returns empty string when session ends."""
        chunk = await self._output_queue.get()
        if not chunk:
            return ""
        return chunk.decode("utf-8", errors="replace")

    async def write(self, data: str) -> None:
        """Write input to the PTY."""
        assert self._process is not None
        self._process.stdin.write(data)

    async def resize(self, cols: int, rows: int) -> None:
        """Resize the PTY."""
        assert self._process is not None
        self._process.change_terminal_size(cols, rows)

    def get_exit_code(self) -> int | None:
        """Return exit code if the process has exited."""
        if self._process and self._process.exit_status is not None:
            return self._process.exit_status
        return None

    async def close(self) -> None:
        """Close the PTY session."""
        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        if self._process and not self._process.is_closing():
            self._process.close()


@router.websocket("/hosts/{host}/terminal")
async def terminal_ws(
    websocket: WebSocket,
    host: str,
    pool: ConnectionPool = Depends(get_pool),
) -> None:
    """WebSocket endpoint for interactive terminal sessions."""
    await websocket.accept()

    # Acquire terminal slot
    if not pool.acquire_terminal_slot(host):
        await websocket.send_json({"type": "error", "message": "Too many terminal sessions for this host"})
        await websocket.close()
        return

    # Register as a live client for heartbeat/liveness tracking
    terminal_client_id = str(uuid.uuid4())
    pool.register_client(host, terminal_client_id, "terminal")

    try:
        conn = await pool.get_connection(host)
    except asyncssh.Error as e:
        await websocket.send_json({"type": "error", "message": f"SSH connection failed: {e}"})
        pool.release_terminal_slot(host)
        await websocket.close()
        return

    term_manager = TerminalManager(conn)

    try:
        await term_manager.start()
        await websocket.send_json({"type": "ready"})

        # Forward output to WebSocket
        async def forward_output() -> None:
            while True:
                output = await term_manager.read()
                if not output:
                    # Session ended
                    exit_code = term_manager.get_exit_code()
                    await websocket.send_json({
                        "type": "exited",
                        "exit_code": exit_code if exit_code is not None else -1,
                    })
                    break
                await websocket.send_json({"type": "output", "data": output})

        output_task = asyncio.create_task(forward_output())

        # Receive input from WebSocket
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "input":
                await term_manager.write(msg.get("data", ""))
            elif msg_type == "resize":
                cols = msg.get("cols", 80)
                rows = msg.get("rows", 24)
                await term_manager.resize(cols, rows)
            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        logger.info("Terminal WebSocket disconnected for host %s", host)
    except asyncssh.DisconnectError:
        await websocket.send_json({"type": "error", "message": "SSH connection lost"})
    except json.JSONDecodeError as e:
        logger.error("Invalid WebSocket message: %s", e)
    except Exception as e:
        logger.error("Terminal error for host %s: %s", host, e)
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        if "output_task" in locals():
            output_task.cancel()
            try:
                await output_task
            except asyncio.CancelledError:
                pass
        await term_manager.close()
        pool.release_terminal_slot(host)
        pool.unregister_client(host, terminal_client_id)
        try:
            await websocket.close()
        except Exception:
            pass
