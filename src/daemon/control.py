"""Control socket for daemon mode — token minting without logs.

The daemon-mode session token must never appear in system logs
(journald / unified log persist stdout). Instead of printing an
authenticated URL at startup, the daemon listens on a Unix domain
socket (0600, same-user only) and mints fresh tokens on demand:

    surf-ssh url   →  connects, mints, prints the URL

Protocol: one JSON request per connection, one JSON response.
    {"command": "mint"}  →  {"url": "https://localhost:PORT/api/v1/auth/exchange?token=…"}
    {"command": "ping"}  →  {"status": "ok"}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from src.security.session_auth import SessionManager

logger = logging.getLogger(__name__)

CONTROL_SOCK_NAME = "control.sock"


class ControlSocketServer:
    """Serves token-minting requests over a Unix domain socket."""

    def __init__(self, session_manager: SessionManager, port: int, config_dir: Path) -> None:
        self._session_manager = session_manager
        self._port = port
        self._sock_path = config_dir / CONTROL_SOCK_NAME
        self._server: asyncio.AbstractServer | None = None

    @property
    def sock_path(self) -> Path:
        return self._sock_path

    async def start(self) -> None:
        """Start listening. Removes stale socket files first."""
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

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._sock_path.unlink(missing_ok=True)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=5.0)
            request = json.loads(raw.decode("utf-8"))
            command = request.get("command")

            if command == "mint":
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
            logger.error("Control socket error: %s", e)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
