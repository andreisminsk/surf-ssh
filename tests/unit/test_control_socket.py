"""Unit tests for the daemon control socket (token minting)."""

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from src.daemon.control import ControlSocketServer
from src.security.session_auth import SessionManager


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


async def _request(sock_path: str, payload: dict) -> dict:
    reader, writer = await asyncio.open_unix_connection(sock_path)
    try:
        writer.write((json.dumps(payload) + "\n").encode())
        await writer.drain()
        raw = await reader.readline()
        return json.loads(raw.decode())
    finally:
        writer.close()
        await writer.wait_closed()


async def test_mint_returns_valid_url(server, session_manager):
    await server.start()
    try:
        response = await _request(str(server.sock_path), {"command": "mint"})
        assert "url" in response
        assert response["url"].startswith("https://localhost:8443/api/v1/auth/exchange?token=")
        # The minted token is a valid session
        token = response["url"].split("token=")[1]
        assert session_manager.validate_session(token) is True
    finally:
        await server.stop()


async def test_ping(server):
    await server.start()
    try:
        response = await _request(str(server.sock_path), {"command": "ping"})
        assert response == {"status": "ok"}
    finally:
        await server.stop()


async def test_unknown_command(server):
    await server.start()
    try:
        response = await _request(str(server.sock_path), {"command": "bogus"})
        assert "error" in response
    finally:
        await server.stop()


async def test_socket_permissions(server):
    import os
    await server.start()
    try:
        mode = server.sock_path.stat().st_mode & 0o777
        assert mode == 0o600
    finally:
        await server.stop()


async def test_stop_removes_socket(server):
    await server.start()
    assert server.sock_path.exists()
    await server.stop()
    assert not server.sock_path.exists()


async def test_stale_socket_replaced(server):
    """A leftover socket file from a crashed daemon is removed on start."""
    server.sock_path.write_text("")  # stale, nothing listening
    await server.start()
    try:
        response = await _request(str(server.sock_path), {"command": "ping"})
        assert response == {"status": "ok"}
    finally:
        await server.stop()


async def test_live_socket_not_stolen(server, session_manager):
    """If another daemon owns the socket, start() must fail loudly."""
    await server.start()
    other = ControlSocketServer(session_manager, port=8444, config_dir=server.sock_path.parent)
    with pytest.raises(RuntimeError, match="already in use"):
        await other.start()
    await server.stop()
