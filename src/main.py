"""CLI entry point for surf-ssh."""

from __future__ import annotations

import sys
import time
import webbrowser
from pathlib import Path

# Windows consoles default to legacy codepages (cp1251/cp1252) that can't
# encode emoji or non-Latin characters used by Rich output.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="surf-ssh",
    help="Local-first browser-based file explorer and terminal for remote SSH machines.",
    no_args_is_help=True,
)
console = Console()

DEFAULT_PORT = 8443
CONFIG_DIR = Path.home() / ".surf-ssh"


@app.command()
def open(
    host: str = typer.Argument(..., help="SSH host alias from ~/.ssh/config"),
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="Local HTTPS port"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open browser"),
    no_auto_exit: bool = typer.Option(
        False, "--no-auto-exit",
        help="Keep daemon running even when no browser clients are connected",
    ),
) -> None:
    """Open a browser-based file explorer for a remote SSH host."""
    import asyncio
    import secrets
    import threading

    from src.daemon.server import create_app
    from src.daemon.tls import ensure_tls_certificates
    from src.security.session_auth import SessionManager

    # Ensure config dir exists
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (CONFIG_DIR / "sessions").mkdir(exist_ok=True)

    # Generate session token
    session_mgr = SessionManager(CONFIG_DIR / "sessions")
    token = session_mgr.create_session()

    # Ensure TLS certificates
    cert_path, key_path = ensure_tls_certificates(CONFIG_DIR)

    # Non-blocking update check — prints a warning if GitHub has a newer version.
    # Runs in a daemon thread so it never delays daemon startup.
    def _check_update() -> None:
        try:
            from src.update_check import check_update

            result = check_update()
        except Exception:
            return
        if result:
            local, remote = result
            console.print(
                f"[yellow]⚠ Update available: surf-ssh {local} → {remote} on GitHub "
                f"(git pull and re-install to upgrade)[/yellow]"
            )

    threading.Thread(target=_check_update, daemon=True).start()

    # Find available port
    actual_port = _find_available_port(port)
    if actual_port != port:
        console.print(f"[yellow]Port {port} in use, using {actual_port} instead[/yellow]")

    url = f"https://localhost:{actual_port}/api/v1/auth/exchange?token={token}&host={host}"

    console.print(f"[green]Starting surf-ssh daemon[/green] → {host}")
    console.print(f"[dim]URL: {url}[/dim]")
    if not no_auto_exit:
        console.print("[dim]Auto-exit enabled — daemon will stop when browser disconnects[/dim]")

    # Run uvicorn in the main thread so it handles Ctrl+C (SIGINT)
    # natively — graceful shutdown, connection draining, lifespan event.
    import asyncio
    import threading
    import uvicorn

    fastapi_app = create_app(session_mgr, auto_exit=not no_auto_exit)
    config = uvicorn.Config(
        fastapi_app,
        host="127.0.0.1",
        port=actual_port,
        ssl_certfile=str(cert_path),
        ssl_keyfile=str(key_path),
        log_level="warning",
    )

    # Prevent uvicorn from installing its own signal handlers — we handle
    # SIGINT ourselves for reliable Ctrl+C on macOS.
    class _NoSignalServer(uvicorn.Server):
        def install_signal_handlers(self) -> None:
            pass

    server = _NoSignalServer(config)

    # Wire auto-exit: the reaper calls this callback when no browser clients
    # remain for the grace period. We set should_exit on the uvicorn server
    # to trigger graceful shutdown (lifespan, connection draining).
    if not no_auto_exit:
        pool = fastapi_app.state.connection_pool
        pool.set_auto_exit(enabled=True, callback=lambda: setattr(server, "should_exit", True))

    # Suppress noisy ConnectionResetError/ConnectionAbortedError on Windows
    # asyncio without swallowing all other exceptions.
    def _filtered_exception_handler(loop, ctx):
        exc = ctx.get("exception")
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError)):
            return
        loop.default_exception_handler(ctx)

    async def _serve():
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(_filtered_exception_handler)
        # Install SIGINT handler on Unix: print feedback, trigger graceful
        # shutdown, then force-exit after 3s if blocked PTY threads prevent
        # clean exit. On Windows, add_signal_handler is unsupported
        # (NotImplementedError) — we use signal.signal() instead, installed
        # before asyncio.run() below.
        import signal
        import os
        if sys.platform != "win32":
            _shutting_down = False

            def _on_sigint() -> None:
                nonlocal _shutting_down
                if _shutting_down:
                    return
                _shutting_down = True
                console.print("\n[yellow]Ctrl+C received — shutting down surf-ssh...[/yellow]")
                server.should_exit = True
                loop.call_later(3.0, lambda: os._exit(0))
            loop.add_signal_handler(signal.SIGINT, _on_sigint)
        await server.serve()

    # Open browser in a short-lived background thread before server starts
    if not no_browser:
        threading.Thread(
            target=lambda: (time.sleep(0.5), webbrowser.open(url)),
            daemon=True,
        ).start()

    # On Windows, install SIGINT handler via signal.signal() before
    # asyncio.run(). This gives immediate feedback and a watchdog thread
    # that force-exits if asyncio.run() hangs during task cancellation
    # (blocked PTY executor threads can prevent clean unwind).
    if sys.platform == "win32":
        import signal
        import os

        _ctrl_c_count = 0

        def _win_sigint(signum=None, frame=None):
            nonlocal _ctrl_c_count
            _ctrl_c_count += 1
            if _ctrl_c_count >= 2:
                console.print("\n[red]Force exit![/red]")
                os._exit(1)
            if _ctrl_c_count == 1:
                console.print("\n[yellow]Ctrl+C received — shutting down surf-ssh...[/yellow]")
                server.should_exit = True
                threading.Thread(
                    target=lambda: (time.sleep(3.0), os._exit(0)),
                    daemon=True,
                 ).start()

        signal.signal(signal.SIGINT, _win_sigint)

    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        # Fallback: if signal handler didn't catch it (e.g. interrupt
        # during asyncio.run startup), handle here.
        console.print("\n[yellow]Shutting down surf-ssh...[/yellow]")
    finally:
        console.print("[green]Goodbye![/green]")
        # Safety net: force exit in case non-daemon executor threads
        # (e.g. blocked PTY reads) keep the process alive.
        import os
        os._exit(0)


@app.command()
def daemon(
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="Local HTTPS port"),
    no_browser: bool = typer.Option(True, "--browser", help="Open browser on startup"),
) -> None:
    """Start the daemon without a specific host — clients pick a host from the UI.

    Designed for LaunchDaemon/systemd scenarios. Uses a strict port:
    if a healthy daemon is already running, exits cleanly (exit 0).
    If the port is occupied by another process, exits with error (exit 1).
    Auto-exit is always off in daemon mode.
    """
    import asyncio
    import socket
    import ssl
    import threading
    import urllib.request

    from src.daemon.server import create_app
    from src.daemon.tls import ensure_tls_certificates
    from src.security.session_auth import SessionManager

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (CONFIG_DIR / "sessions").mkdir(exist_ok=True)

    session_mgr = SessionManager(CONFIG_DIR / "sessions")
    cert_path, key_path = ensure_tls_certificates(CONFIG_DIR)

    # Option B: check if a healthy daemon is already running on this port
    if _is_daemon_running(port, cert_path):
        console.print(f"[green]Daemon already running on port {port}[/green]")
        raise typer.Exit(0)

    # Strict port: fail if occupied by something else
    if _is_port_occupied(port):
        console.print(f"[red]Port {port} is in use by another process[/red]")
        raise typer.Exit(1)

    url = f"https://localhost:{port}/ui"

    console.print(f"[green]Starting surf-ssh daemon[/green] (no host — client picks from UI)")
    console.print(f"[dim]URL: {url}[/dim]")

    import uvicorn

    fastapi_app = create_app(session_mgr, auto_exit=False)
    config = uvicorn.Config(
        fastapi_app,
        host="127.0.0.1",
        port=port,
        ssl_certfile=str(cert_path),
        ssl_keyfile=str(key_path),
        log_level="warning",
    )

    class _NoSignalServer(uvicorn.Server):
        def install_signal_handlers(self) -> None:
            pass

    server = _NoSignalServer(config)

    def _filtered_exception_handler(loop, ctx):
        exc = ctx.get("exception")
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError)):
            return
        loop.default_exception_handler(ctx)

    async def _serve():
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(_filtered_exception_handler)
        import signal
        import os
        if sys.platform != "win32":
            _shutting_down = False

            def _on_sigint() -> None:
                nonlocal _shutting_down
                if _shutting_down:
                    return
                _shutting_down = True
                console.print("\n[yellow]Ctrl+C received — shutting down surf-ssh...[/yellow]")
                server.should_exit = True
                loop.call_later(3.0, lambda: os._exit(0))
            loop.add_signal_handler(signal.SIGINT, _on_sigint)
        await server.serve()

    if not no_browser:
        threading.Thread(
            target=lambda: (time.sleep(0.5), webbrowser.open(url)),
            daemon=True,
        ).start()

    if sys.platform == "win32":
        import signal
        import os

        _ctrl_c_count = 0

        def _win_sigint(signum=None, frame=None):
            nonlocal _ctrl_c_count
            _ctrl_c_count += 1
            if _ctrl_c_count >= 2:
                console.print("\n[red]Force exit![/red]")
                os._exit(1)
            if _ctrl_c_count == 1:
                console.print("\n[yellow]Ctrl+C received — shutting down surf-ssh...[/yellow]")
                server.should_exit = True
                threading.Thread(
                    target=lambda: (time.sleep(3.0), os._exit(0)),
                    daemon=True,
                ).start()

        signal.signal(signal.SIGINT, _win_sigint)

    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        console.print("\n[yellow]Shutting down surf-ssh...[/yellow]")
    finally:
        console.print("[green]Goodbye![/green]")
        import os
        os._exit(0)


@app.command()
def hosts() -> None:
    """List available SSH host aliases from ~/.ssh/config."""
    from src.ssh.config_parser import SSHConfigParser

    parser = SSHConfigParser()
    all_hosts = parser.list_hosts()

    table = Table(title="Available SSH Hosts")
    table.add_column("Alias", style="cyan")
    table.add_column("HostName", style="green")
    table.add_column("User", style="yellow")
    table.add_column("ProxyJump", style="magenta")

    for h in all_hosts:
        config = parser.get_host_config(h)
        table.add_row(
            h,
            config.get("hostname", ""),
            config.get("user", ""),
            config.get("proxyjump", "") or "",
        )

    console.print(table)


def _find_available_port(start: int) -> int:
    import socket

    for port in range(start, start + 100):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    console.print("[red]No available ports found[/red]")
    raise typer.Exit(1)


def _is_port_occupied(port: int) -> bool:
    """Check if a TCP port is in use."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True


def _is_daemon_running(port: int, cert_path: Path) -> bool:
    """Check if a healthy surf-ssh daemon is already running on the port.

    Hits /api/v1/health over HTTPS with the self-signed cert.
    Returns True if the response is 200, False otherwise.
    """
    import ssl
    import urllib.request
    ctx = ssl.create_default_context(cafile=str(cert_path))
    ctx.check_hostname = False
    try:
        with urllib.request.urlopen(
            f"https://localhost:{port}/api/v1/health",
            context=ctx,
            timeout=2,
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


if __name__ == "__main__":
    app()
