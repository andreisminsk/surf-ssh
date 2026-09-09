"""Integration tests for API endpoints using FastAPI TestClient."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.daemon.server import create_app
from src.daemon.static import serve_spa_asset
from src.security.session_auth import SessionManager


@pytest.fixture
def session_manager(tmp_path):
    return SessionManager(tmp_path / "sessions")


@pytest.fixture
def token(session_manager):
    return session_manager.create_session()


@pytest.fixture
def client(session_manager):
    app = create_app(session_manager)
    return TestClient(app)


@pytest.fixture
def auth_client(client, token, session_manager):
    """Client with session cookie set and auth bypassed for testing."""
    # Mock validate_session so we don't depend on cookie transport mechanics
    session_manager.validate_session = lambda t: t == token
    client.cookies.set("surf_ssh_session", token)
    return client


class TestHealthEndpoint:
    """Tests for the health check endpoint."""

    def test_health_no_auth_required(self, client):
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestAuthExchange:
    """Tests for the token exchange endpoint."""

    def test_exchange_valid_token(self, client, token):
        resp = client.get(f"/api/v1/auth/exchange?token={token}", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/ui"
        # Cookie should be set
        cookies = resp.headers.get("set-cookie", "")
        assert "surf_ssh_session" in cookies
        assert "HttpOnly" in cookies
        assert "Secure" in cookies
        assert "samesite=strict" in cookies.lower()

    def test_exchange_preserves_host_param(self, client, token):
        resp = client.get(
            f"/api/v1/auth/exchange?token={token}&host=mac-remote",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "host=mac-remote" in resp.headers["location"]

    def test_exchange_invalid_token(self, client):
        resp = client.get("/api/v1/auth/exchange?token=invalid", follow_redirects=False)
        assert resp.status_code == 401

    def test_exchange_missing_token(self, client):
        resp = client.get("/api/v1/auth/exchange", follow_redirects=False)
        assert resp.status_code == 422  # Missing required param


class TestAuthEnforcement:
    """Tests for authentication middleware."""

    def test_api_requires_auth(self, client):
        resp = client.get("/api/v1/hosts")
        assert resp.status_code == 401

    def test_api_with_valid_cookie(self, auth_client):
        # Will fail because no SSH config, but should not be 401
        resp = auth_client.get("/api/v1/hosts")
        assert resp.status_code != 401

    def test_ui_served_without_auth(self, client):
        # UI should be accessible without auth (it's the entry point)
        resp = client.get("/ui")
        # 404 is OK here — UI not built in test env
        assert resp.status_code in (200, 404)


class TestPathValidation:
    """Tests for path validation in API endpoints."""

    def test_tree_rejects_traversal(self, auth_client):
        resp = auth_client.get("/api/v1/hosts/test-host/tree?path=../../etc/passwd")
        assert resp.status_code == 400
        assert "traversal" in resp.json()["detail"].lower()

    def test_file_rejects_traversal(self, auth_client):
        resp = auth_client.get("/api/v1/hosts/test-host/file?path=../../etc/passwd")
        assert resp.status_code == 400
        assert "traversal" in resp.json()["detail"].lower()

    def test_stat_rejects_traversal(self, auth_client):
        resp = auth_client.get("/api/v1/hosts/test-host/stat?path=../../etc/passwd")
        assert resp.status_code == 400
        assert "traversal" in resp.json()["detail"].lower()

    def test_download_rejects_traversal(self, auth_client):
        resp = auth_client.get("/api/v1/hosts/test-host/download?path=../../etc/passwd")
        assert resp.status_code == 400
        assert "traversal" in resp.json()["detail"].lower()


class TestTreeEndpoint:
    """Tests for the tree endpoint parameters."""

    def test_tree_depth_limit_enforced(self, auth_client):
        # depth > MAX_DEPTH (2) should be rejected
        resp = auth_client.get("/api/v1/hosts/test-host/tree?path=/&depth=5")
        assert resp.status_code == 422

    def test_tree_depth_minimum(self, auth_client):
        resp = auth_client.get("/api/v1/hosts/test-host/tree?path=/&depth=0")
        assert resp.status_code == 422

    def test_tree_limit_capped(self, auth_client):
        # limit > MAX_LIMIT (5000) should be rejected
        resp = auth_client.get("/api/v1/hosts/test-host/tree?path=/&limit=99999")
        assert resp.status_code == 422


class TestWsAuth:
    """WebSocket endpoints must reject unauthenticated handshakes.

    BaseHTTPMiddleware never sees WS scopes, so each WS endpoint calls
    require_ws_auth() before accept(). A rejected handshake raises
    WebSocketDisconnect in the TestClient.
    """

    def test_liveness_ws_requires_auth(self, client):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/v1/liveness?host=test"):
                pass

    def test_local_terminal_ws_requires_auth(self, client):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/v1/local/terminal?shell=default"):
                pass

    def test_remote_terminal_ws_requires_auth(self, client):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/v1/hosts/test-host/terminal"):
                pass

    def test_liveness_ws_rejects_bad_token_query_param(self, client):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/v1/liveness?host=test&token=bogus"):
                pass

    def test_liveness_ws_accepts_valid_cookie(self, auth_client):
        with auth_client.websocket_connect("/api/v1/liveness?host=test") as ws:
            assert ws.receive_json() == {"type": "ready"}


class TestOpenApiDocsDisabled:
    """OpenAPI docs must not be exposed unless SURF_SSH_DEV=1."""

    def test_docs_404_by_default(self, auth_client):
        # Route removed entirely — 404 even when authenticated
        assert auth_client.get("/api/v1/docs").status_code == 404
        assert auth_client.get("/api/v1/openapi.json").status_code == 404

    def test_docs_unauthenticated_gets_401(self, client):
        # Unknown /api/v1/* paths are intercepted by the auth middleware
        assert client.get("/api/v1/docs").status_code == 401


class TestStaticTraversal:
    """The /ui/{path} handler must contain all paths inside ui/dist."""

    @pytest.mark.asyncio
    async def test_encoded_traversal_rejected(self):
        # "..%2f..%2f" arrives URL-decoded as "../../"
        with pytest.raises(HTTPException) as exc:
            await serve_spa_asset("../../README.md")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_absolute_path_injection_rejected(self):
        # Path("/x") / "/etc/passwd" → "/etc/passwd" — must be caught
        with pytest.raises(HTTPException) as exc:
            await serve_spa_asset("/etc/passwd")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_deep_traversal_rejected(self):
        with pytest.raises(HTTPException) as exc:
            await serve_spa_asset("assets/../../../../../../../etc/passwd")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_legit_asset_still_served(self):
        # ui/dist/index.html exists in the repo — should be served
        resp = await serve_spa_asset("index.html")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_spa_fallback_still_works(self):
        # Unknown client-side route falls back to index.html, not 404
        resp = await serve_spa_asset("some/client/route")
        assert resp.status_code == 200
