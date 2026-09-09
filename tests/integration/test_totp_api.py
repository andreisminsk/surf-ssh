"""Integration tests for the TOTP verify endpoint."""

import json

import pyotp
import pytest
from fastapi.testclient import TestClient

from src.daemon.server import create_app
from src.security.session_auth import SessionManager
from src.security.totp import TotpManager


@pytest.fixture
def totp_manager(tmp_path):
    return TotpManager(tmp_path)


@pytest.fixture
def client(tmp_path, totp_manager):
    session_manager = SessionManager(tmp_path / "sessions")
    app = create_app(session_manager)
    # Point the app at our test TOTP manager (create_app uses ~/.surf-ssh)
    app.state.totp_manager = totp_manager
    # Secure cookies (secure=True) are only sent over https
    return TestClient(app, base_url="https://testserver")


@pytest.fixture
def enrolled(client, totp_manager):
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    assert totp_manager.confirm_setup(secret, totp.now(), ["aaaa-bbbb"]) is True
    return totp


class TestVerifyEndpoint:
    def test_valid_code_sets_cookie(self, client, enrolled):
        resp = client.post(
            "/api/v1/auth/verify-totp",
            json={"code": enrolled.now()},
        )
        assert resp.status_code == 200
        assert "surf_ssh_session" in resp.cookies
        # The cookie actually authenticates
        hosts_resp = client.get("/api/v1/hosts")
        assert hosts_resp.status_code != 401

    def test_invalid_code_401(self, client, enrolled):
        resp = client.post("/api/v1/auth/verify-totp", json={"code": "000000"})
        assert resp.status_code == 401

    def test_backup_code_sets_cookie(self, client, enrolled):
        resp = client.post(
            "/api/v1/auth/verify-totp",
            json={"backup_code": "aaaa-bbbb"},
        )
        assert resp.status_code == 200
        assert "surf_ssh_session" in resp.cookies

    def test_2fa_not_enabled_404(self, client):
        resp = client.post("/api/v1/auth/verify-totp", json={"code": "123456"})
        assert resp.status_code == 404

    def test_missing_body_422(self, client, enrolled):
        resp = client.post("/api/v1/auth/verify-totp", json={})
        assert resp.status_code == 422

    def test_rate_limit_429(self, client, enrolled):
        for _ in range(5):
            client.post("/api/v1/auth/verify-totp", json={"code": "000000"})
        resp = client.post("/api/v1/auth/verify-totp", json={"code": "000000"})
        assert resp.status_code == 429
        assert resp.headers["retry-after"].isdigit()

    def test_endpoint_exempt_from_cookie_auth(self, client, enrolled):
        # No cookie at all — the endpoint must still be reachable
        resp = client.post("/api/v1/auth/verify-totp", json={"code": "000000"})
        assert resp.status_code == 401  # invalid code, but NOT middleware-401
