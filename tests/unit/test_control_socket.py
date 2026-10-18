"""Unit tests for the daemon control channel (token minting)."""

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

from src.daemon.control import (
    CONTROL_PORT_NAME,
    CONTROL_SECRET_NAME,
    ControlSocketServer,
    control_request,
)
from src.security.session_auth import SessionManager

IS_WINDOWS = sys.platform == "win32"


@pytest.fixture
def short_tmp_path():
    """Unix domain socket paths are limited (~104 chars); pytest's default
    tmp_path is too deep. Use a short /tmp-based directory instead."""
    path = Path(tempfile.mkdtemp(prefix="surf-ctl-"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def session_manager(short_tmp_path):
    return SessionManager(short_tmp_path / "sessions")


@pytest.fixture
def server(session_manager, short_tmp_path):
    return ControlSocketServer(session_manager, port=8443, config_dir=short_tmp_path)


async def test_mint_returns_valid_url(server, session_manager, short_tmp_path):
    await server.start()
    try:
        response = await control_request(short_tmp_path, {"command": "mint"})
        assert "url" in response
        assert response["url"].startswith("https://localhost:8443/api/v1/auth/exchange?token=")
        # The minted token is a valid session
        token = response["url"].split("token=")[1]
        assert session_manager.validate_session(token) is True
    finally:
        await server.stop()


async def test_ping(server, short_tmp_path):
    await server.start()
    try:
        response = await control_request(short_tmp_path, {"command": "ping"})
        assert response == {"status": "ok"}
    finally:
        await server.stop()


async def test_unknown_command(server, short_tmp_path):
    await server.start()
    try:
        response = await control_request(short_tmp_path, {"command": "bogus"})
        assert "error" in response
    finally:
        await server.stop()


@pytest.mark.skipif(IS_WINDOWS, reason="0600 permissions are POSIX-only")
async def test_socket_permissions(server):
    import os
    await server.start()
    try:
        mode = server.sock_path.stat().st_mode & 0o777
        assert mode == 0o600
    finally:
        await server.stop()


async def test_stop_removes_channel(server, short_tmp_path):
    await server.start()
    if IS_WINDOWS:
        assert (short_tmp_path / CONTROL_PORT_NAME).exists()
        assert (short_tmp_path / CONTROL_SECRET_NAME).exists()
    else:
        assert server.sock_path.exists()
    await server.stop()
    if IS_WINDOWS:
        assert not (short_tmp_path / CONTROL_PORT_NAME).exists()
        assert not (short_tmp_path / CONTROL_SECRET_NAME).exists()
    else:
        assert not server.sock_path.exists()


@pytest.mark.skipif(IS_WINDOWS, reason="stale unix-socket handshake is POSIX-only")
async def test_stale_socket_replaced(server):
    """A leftover socket file from a crashed daemon is removed on start."""
    server.sock_path.write_text("")  # stale, nothing listening
    await server.start()
    try:
        response = await control_request(server.sock_path.parent, {"command": "ping"})
        assert response == {"status": "ok"}
    finally:
        await server.stop()


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows transport files")
async def test_stale_transport_files_overwritten(server, short_tmp_path):
    """Leftover port/secret files from a crashed daemon are replaced on start."""
    (short_tmp_path / CONTROL_PORT_NAME).write_text("99999")
    (short_tmp_path / CONTROL_SECRET_NAME).write_text("stale-secret")
    await server.start()
    try:
        response = await control_request(short_tmp_path, {"command": "ping"})
        assert response == {"status": "ok"}
    finally:
        await server.stop()


@pytest.mark.skipif(not IS_WINDOWS, reason="secret auth is Windows-transport-only")
async def test_tcp_requires_secret(server, short_tmp_path):
    """TCP control channel rejects requests without the shared secret."""
    await server.start()
    try:
        port = int((short_tmp_path / CONTROL_PORT_NAME).read_text())
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write((json.dumps({"command": "mint"}) + "\n").encode())
            await writer.drain()
            raw = await asyncio.wait_for(reader.readline(), timeout=5.0)
            assert json.loads(raw.decode()).get("error") == "unauthorized"
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
    finally:
        await server.stop()


@pytest.mark.skipif(IS_WINDOWS, reason="fixed socket path theft is POSIX-only")
async def test_live_socket_not_stolen(server, session_manager):
    """If another daemon owns the socket, start() must fail loudly."""
    await server.start()
    other = ControlSocketServer(session_manager, port=8444, config_dir=server.sock_path.parent)
    with pytest.raises(RuntimeError, match="already in use"):
        await other.start()
    await server.stop()
