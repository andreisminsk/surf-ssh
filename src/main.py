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


def _force_exit_watchdog(delay: float) -> None:
    """Force-exit the process after `delay` seconds, from a real thread.

    A loop.call_later watchdog never fires if the event loop is blocked
    in an uninterruptible syscall (e.g. a PTY close hanging in U state
    on macOS) — a daemon thread always fires.
    """
    import os
    import threading

    timer = threading.Timer(delay, os._exit, (0,))
    timer.daemon = True
    timer.start()


@app.command()
def open(
    host: str = typer.Argument(None, help="SSH host alias from ~/.ssh/config"),
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="Local HTTPS port"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open browser"),
    no_auto_exit: bool = typer.Option(
        False, "--no-auto-exit",
        help="Keep daemon running even when no browser clients are connected",
    ),
    no_update_check: bool = typer.Option(
        False, "--no-update-check",
        help="Skip the outbound update check to GitHub",
    ),
    cmd: str = typer.Option(None, "--cmd", help="Parse an ssh/scp command string into a config entry and open it"),
    save: str = typer.Option(None, "--save", help="Save the --cmd config entry under this name (persistent). Without --save, an ephemeral entry is created and removed on exit."),
) -> None:
    """Open a browser-based file explorer for a remote SSH host."""
    import asyncio
    import secrets
    import threading

    from src.daemon.server import create_app
    from src.daemon.tls import ensure_tls_certificates
    from src.security.session_auth import SessionManager
    from src.ssh.config_parser import SSHConfigParser
    from src.ssh.config_editor import ConfigEditor
    from src.ssh.cmd_parser import CmdParseError, parse_ssh_command
    from src.ssh.naming import NameCollisionError, generate_phonetic, validate_user_name

    # Startup cleanup: remove ephemeral entries orphaned by a previous crash
    try:
        ConfigEditor().remove_all_ephemeral()
    except Exception:
        pass

    # --cmd mode: parse the command, generate a name, write a config entry
    _ephemeral_name: str | None = None

    if cmd:
        try:
            parsed = parse_ssh_command(cmd)
        except CmdParseError as e:
            console.print(f"[red]Cannot parse command: {e}[/red]")
            raise typer.Exit(1)

        existing_hosts = set(SSHConfigParser().list_hosts())

        if save:
            try:
                name = validate_user_name(save, existing_hosts)
                tag = "persistent"
            except NameCollisionError as e:
                console.print(f"[red]{e}[/red]")
                raise typer.Exit(1)
        else:
            name = generate_phonetic(existing_hosts)
            tag = "ephemeral"
            _ephemeral_name = name

        editor = ConfigEditor()
        editor.append_entry(name, parsed, tag=tag)
        console.print(f"[green]Created config entry:[/green] {name}")
        if save:
            console.print(f"[dim]Saved permanently in ~/.ssh/config as '{name}'[/dim]")
        else:
            console.print(f"[dim]Ephemeral — will be removed on exit[/dim]")

        host = name
        _adhoc_spec = None
    elif host is None:
        console.print("[red]Provide a host alias or use --cmd[/red]")
        raise typer.Exit(1)
    elif "@" in host or not SSHConfigParser().list_hosts().count(host):
        import getpass

        spec = host
        user_part, _, host_part = spec.partition("@")
        hostname = user_part if not host_part else host_part
        user = user_part if host_part else None
        port = 22
        if ":" in hostname:
            hostname, _, port_s = hostname.rpartition(":")
            if port_s.isdigit():
                port = int(port_s)

        console.print(f"[green]Starting surf-ssh daemon[/green] → {hostname} (ad-hoc, password auth)")
        password = getpass.getpass(f"Password for {user or hostname}: ")

        # Register on the app's pool once created
        _adhoc_spec = (hostname, user, port, password)
    else:
        _adhoc_spec = None

    # Ensure config dir exists
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    (CONFIG_DIR / "sessions").mkdir(exist_ok=True)

    # Generate session token
    session_mgr = SessionManager(CONFIG_DIR / "sessions")
    token = session_mgr.create_session()

    # Ensure TLS certificates
    cert_path, key_path = ensure_tls_certificates(CONFIG_DIR)

    # Non-blocking update check — prints a warning if GitHub has a newer version.
    # Runs in a daemon thread so it never delays daemon startup. Skipped with
    # --no-update-check or when a check has run within the last 24h (cached).
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

    if not no_update_check:
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

    if _adhoc_spec is not None:
        hostname, user, port, password = _adhoc_spec
        pool = fastapi_app.state.connection_pool
        alias = pool.register_adhoc(hostname, user, port)
        pool.set_password(alias, password)
        host = alias  # browser opens against the synthetic alias
        console.print(f"[dim]Ad-hoc alias: {alias}[/dim]")
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

        def _auto_exit() -> None:
            # Graceful shutdown, with a hard force-exit watchdog: if
            # graceful shutdown hangs (e.g. a blocked PTY close), the
            # watchdog thread still terminates the process.
            server.should_exit = True
            _force_exit_watchdog(10.0)

        pool.set_auto_exit(enabled=True, callback=_auto_exit)

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
                _force_exit_watchdog(3.0)
            loop.add_signal_handler(signal.SIGINT, _on_sigint)
            # SIGTERM (kill, service manager stop) — same graceful path
            loop.add_signal_handler(signal.SIGTERM, _on_sigint)
        await server.serve()

    # Open browser in a short-lived background thread before server starts
    if not no_browser:
        # open mode: the one-shot token URL is already in hand — open it
        # directly (no control socket exists in this mode).
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

    exit_code = 0
    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        # Fallback: if signal handler didn't catch it (e.g. interrupt
        # during asyncio.run startup), handle here.
        console.print("\n[yellow]Shutting down surf-ssh...[/yellow]")
    except Exception:
        # Surface the real error — the force-exit below would otherwise
        # swallow the traceback and make a crash look like a clean exit.
        console.print_exception()
        exit_code = 1
    finally:
        # Remove ephemeral config entries created by --cmd (without --save)
        if _ephemeral_name:
            try:
                ConfigEditor().remove_entry(_ephemeral_name)
            except Exception:
                pass
        console.print("[green]Goodbye![/green]")
        # Safety net: force exit in case non-daemon executor threads
        # (e.g. blocked PTY reads) keep the process alive.
        import os
        os._exit(exit_code)


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

    # Startup cleanup: remove ephemeral entries orphaned by a previous crash
    try:
        from src.ssh.config_editor import ConfigEditor
        ConfigEditor().remove_all_ephemeral()
    except Exception:
        pass

    # Option B: check if a healthy daemon is already running on this port
    if _is_daemon_running(port, cert_path):
        console.print(f"[green]Daemon already running on port {port}[/green]")
        raise typer.Exit(0)

    # Strict port: fail if occupied by something else
    if _is_port_occupied(port):
        console.print(f"[red]Port {port} is in use by another process[/red]")
        raise typer.Exit(1)

    # Control socket: mints session tokens on demand via `surf-ssh url`.
    # The token never appears in daemon stdout (system logs persist it).
    from src.daemon.control import ControlSocketServer

    control_server = ControlSocketServer(session_mgr, port, CONFIG_DIR)

    console.print(f"[green]Starting surf-ssh daemon[/green] (no host — client picks from UI)")
    console.print(f"[dim]Port: {port} — connect with: surf-ssh url[/dim]")

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
                _force_exit_watchdog(3.0)
            loop.add_signal_handler(signal.SIGINT, _on_sigint)
        await control_server.start()
        try:
            await server.serve()
        finally:
            await control_server.stop()

    if not no_browser:
        # Open a browser with a freshly minted token — the token goes
        # straight to the browser, never to stdout/logs.
        def _open_browser() -> None:
            try:
                minted = _mint_url_via_socket(timeout=10.0)
                if minted:
                    webbrowser.open(minted)
            except Exception:
                pass

        threading.Thread(target=lambda: (time.sleep(0.5), _open_browser()), daemon=True).start()

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

    exit_code = 0
    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        console.print("\n[yellow]Shutting down surf-ssh...[/yellow]")
    except Exception:
        # Surface the real error — the force-exit below would otherwise
        # swallow the traceback and make a crash look like a clean exit.
        console.print_exception()
        exit_code = 1
    finally:
        console.print("[green]Goodbye![/green]")
        import os
        os._exit(exit_code)


def _mint_url_via_socket(timeout: float = 5.0) -> str | None:
    """Ask the running daemon to mint a fresh session URL.

    Connects to the daemon's control channel (Unix socket on POSIX,
    secret-authenticated TCP loopback on Windows) and sends
    {"command": "mint"}. Returns the URL or None if no daemon is
    listening.
    """
    import asyncio
    import json

    from src.daemon.control import control_request

    async def _request() -> str | None:
        try:
            response = await asyncio.wait_for(
                control_request(CONFIG_DIR, {"command": "mint"}), timeout=timeout
            )
        except (
            FileNotFoundError, ConnectionError, OSError,
            asyncio.TimeoutError, json.JSONDecodeError, UnicodeDecodeError,
        ):
            return None
        return response.get("url")

    return asyncio.run(_request())


@app.command()
def url(
    open_browser: bool = typer.Option(False, "--open", help="Open the URL in a browser"),
) -> None:
    """Print a fresh authenticated URL for the running daemon.

    Asks the daemon (via its control socket) to mint a new session
    token. The token appears only in this terminal — never in the
    daemon's stdout or system logs.
    """
    result = _mint_url_via_socket()
    if not result:
        console.print("[red]No running daemon found[/red] — start one with: surf-ssh daemon")
        raise typer.Exit(1)
    console.print(result)
    if open_browser:
        webbrowser.open(result)


@app.command()
def setup_2fa() -> None:
    """Enable TOTP 2FA for daemon-mode browser access.

    Prints a QR code to scan with Google Authenticator (or any TOTP app),
    then asks for one code to confirm. Also generates 10 single-use
    backup codes — shown ONCE, save them now.
    """
    import qrcode

    from src.security.totp import TotpManager

    mgr = TotpManager(CONFIG_DIR)
    if mgr.is_enabled():
        console.print("[yellow]2FA is already enabled.[/yellow] Disable first with: surf-ssh disable-2fa")
        raise typer.Exit(1)

    secret, uri, backup_codes = mgr.setup()

    console.print("[green]Scan this QR code with your authenticator app:[/green]\n")
    qr = qrcode.QRCode(border=1)
    qr.add_data(uri)
    qr.print_ascii(out=console.file)
    console.print(f"\n[dim]Manual entry secret: {secret}[/dim]")

    code = typer.prompt("Enter the 6-digit code to confirm")
    if not mgr.confirm_setup(secret, code, backup_codes):
        console.print("[red]Code did not verify — 2FA not enabled.[/red]")
        raise typer.Exit(1)

    console.print("[green]2FA enabled.[/green]")
    console.print("\n[yellow]Backup codes (shown once — save them now):[/yellow]")
    for bc in backup_codes:
        console.print(f"  [cyan]{bc}[/cyan]")
    console.print(
        "\n[dim]Browser access: open https://localhost:8443 and enter a code. "
        "CLI access is unchanged: surf-ssh url[/dim]"
    )


@app.command()
def disable_2fa() -> None:
    """Disable 2FA (recovery path — removes the TOTP secret)."""
    from src.security.totp import TotpManager

    mgr = TotpManager(CONFIG_DIR)
    if not mgr.is_enabled():
        console.print("2FA is not enabled.")
        raise typer.Exit(0)
    if not typer.confirm("Disable 2FA? The TOTP secret will be deleted"):
        raise typer.Exit(0)
    mgr.disable()
    console.print("[green]2FA disabled.[/green] Direct browser access now requires surf-ssh url.")


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
