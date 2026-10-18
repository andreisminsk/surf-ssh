"""Control channel for daemon mode — token minting without logs.

The daemon-mode session token must never appear in system logs
(journald / unified log persist stdout). Instead of printing an
authenticated URL at startup, the daemon listens on a local control
channel and mints fresh tokens on demand:

    surf-ssh url   →  connects, mints, prints the URL

Protocol: one JSON request per connection, one JSON response.
    {"command": "mint"}  →  {"url": "https://localhost:PORT/api/v1/auth/exchange?token=…"}
    {"command": "ping"}  →  {"status": "ok"}

Transport (platform-dependent):
- POSIX: Unix domain socket at ~/.surf-ssh/control.sock (0600) —
  kernel-enforced same-user guarantee.
- Windows: TCP loopback on an ephemeral port + shared secret file.
  asyncio has no Unix-socket support on win32 (start_unix_server /
  open_unix_connection do not exist) — using them crashed the daemon
  at startup. The secret lives in ~/.surf-ssh/control.secret; the user
  profile directory is ACL-protected against other local users, giving
  the same same-user trust level as the POSIX socket. Single-daemon
  exclusivity is already guaranteed by the strict HTTPS port check,
  so the ephemeral control port cannot collide.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import sys
from pathlib import Path

from src.security.session_auth import SessionManager

logger = logging.getLogger(__name__)

CONTROL_SOCK_NAME = "control.sock"       # POSIX transport
CONTROL_PORT_NAME = "control.port"       # Windows: ephemeral port file
CONTROL_SECRET_NAME = "control.secret"   # Windows: shared secret file


class ControlSocketServer:
    """Serves token-minting requests over a local control channel."""

    def __init__(self, session_manager: SessionManager, port: int, config_dir: Path) -> None:
        self._session_manager = session_manager
        self._port = port
        self._config_dir = config_dir
        self._sock_path = config_dir / CONTROL_SOCK_NAME
        self._server: asyncio.AbstractServer | None = None
        self._is_windows = sys.platform == "win32"
        self._secret = secrets.token_urlsafe(32)

    @property
    def sock_path(self) -> Path:
        return self._sock_path

    async def start(self) -> None:
        """Start listening. Removes stale socket files first (POSIX)."""
        if self._is_windows:
            await self._start_tcp()
        else:
            await self._start_unix()

    async def _start_unix(self) -> None:
        import stat

        if self._sock_path.exists():
            if not stat.S_ISSOCK(self._sock_path.stat().st_mode):
                # Regular file left behind — remove it.
                self._sock_path.unlink(missing_ok=True)
            else:
                # Socket file from a previous run. If a live daemon still
                # owns it, refuse to steal it; otherwise remove the corpse.
                try:
                    reader, writer = await asyncio.wait_for(
                        asyncio.open_unix_connection(str(self._sock_path)), timeout=1.0
                    )
                    writer.close()
                    await writer.wait_closed()
                    # Connection succeeded — a live daemon owns this socket.
                    raise RuntimeError(f"control socket already in use: {self._sock_path}")
                except (FileNotFoundError, ConnectionError, OSError, asyncio.TimeoutError):
                    self._sock_path.unlink(missing_ok=True)

        self._server = await asyncio.start_unix_server(
            self._handle_client, str(self._sock_path)
        )
        os.chmod(self._sock_path, 0o600)
        logger.info("Control socket listening at %s", self._sock_path)

    async def _start_tcp(self) -> None:
        """Windows transport: TCP loopback, ephemeral port, shared secret.

        No stale-socket handshake needed: the HTTPS strict-port check
        already guarantees a single daemon, and the port is ephemeral —
        leftover files from a crashed daemon are simply overwritten.
        """
        self._server = await asyncio.start_server(self._handle_client, "127.0.0.1", 0)
        sockets = self._server.sockets or []
        if not sockets:
            raise RuntimeError("control channel failed to bind")
        bound_port = sockets[0].getsockname()[1]
        self._config_dir.mkdir(parents=True, exist_ok=True)
        (self._config_dir / CONTROL_PORT_NAME).write_text(str(bound_port), encoding="utf-8")
        secret_file = self._config_dir / CONTROL_SECRET_NAME
        secret_file.write_text(self._secret, encoding="utf-8")
        os.chmod(secret_file, 0o600)
        logger.info(
            "Control channel listening on 127.0.0.1:%s (secret-authenticated)", bound_port
        )

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._is_windows:
            (self._config_dir / CONTROL_PORT_NAME).unlink(missing_ok=True)
            (self._config_dir / CONTROL_SECRET_NAME).unlink(missing_ok=True)
        else:
            self._sock_path.unlink(missing_ok=True)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=5.0)
            request = json.loads(raw.decode("utf-8"))
            command = request.get("command")

            if self._is_windows and not secrets.compare_digest(
                str(request.get("secret", "")), self._secret
            ):
                # TCP loopback is reachable by any local process — the
                # shared secret (readable only by the same user) is the
                # same-user gate that the POSIX socket gets from the kernel.
                response = {"error": "unauthorized"}
            elif command == "mint":
                token = self._session_manager.create_session()
                url = f"https://localhost:{self._port}/api/v1/auth/exchange?token={token}"
                response = {"url": url}
            elif command == "ping":
                response = {"status": "ok"}
            else:
                response = {"error": f"unknown command: {command}"}

            writer.write((json.dumps(response) + "\n").encode("utf-8"))
            await writer.drain()
        except (json.JSONDecodeError, asyncio.TimeoutError, UnicodeDecodeError) as e:
            try:
                writer.write((json.dumps({"error": str(e)}) + "\n").encode("utf-8"))
                await writer.drain()
            except Exception:
                pass
        except Exception as e:
            logger.error("Control channel error: %s", e)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


async def control_request(config_dir: Path, payload: dict, timeout: float = 5.0) -> dict:
    """Send one JSON request to the running daemon's control channel.

    Platform-aware client: Unix socket on POSIX, secret-authenticated
    TCP loopback on Windows. Raises FileNotFoundError/ConnectionError/
    OSError if no daemon is listening, asyncio.TimeoutError on timeout,
    json.JSONDecodeError on a malformed reply.
    """
    if sys.platform == "win32":
        port = int((config_dir / CONTROL_PORT_NAME).read_text(encoding="utf-8").strip())
        secret = (config_dir / CONTROL_SECRET_NAME).read_text(encoding="utf-8").strip()
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port), timeout=timeout
        )
        payload = {**payload, "secret": secret}
    else:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(str(config_dir / CONTROL_SOCK_NAME)), timeout=timeout
        )
    try:
        writer.write((json.dumps(payload) + "\n").encode("utf-8"))
        await writer.drain()
        raw = await asyncio.wait_for(reader.readline(), timeout=timeout)
        return json.loads(raw.decode("utf-8"))
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
