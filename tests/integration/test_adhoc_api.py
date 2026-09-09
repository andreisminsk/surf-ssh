"""Integration tests for ad-hoc host endpoints — session-protected, taxonomy."""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.daemon.server import create_app
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
def auth_client(client, token):
    client.cookies.set("surf_ssh_session", token)
    return client


class TestSessionProtection:
    """The brute-force-oracle guard: all ad-hoc endpoints require a session."""

    def test_add_adhoc_requires_auth(self, client):
        resp = client.post("/api/v1/hosts/adhoc", json={"hostname": "example.com"})
        assert resp.status_code == 401

    def test_auth_requires_session(self, client):
        resp = client.post(
            "/api/v1/hosts/adhoc:alice@example.com/auth",
            json={"password": "secret"},
        )
        assert resp.status_code == 401

    def test_trust_requires_session(self, client):
        resp = client.post(
            "/api/v1/hosts/adhoc:alice@example.com/trust",
            json={"key_data": "ssh-ed25519 AAAAkey"},
        )
        assert resp.status_code == 401


class TestAddAdhoc:
    def test_add_returns_alias(self, auth_client):
        resp = auth_client.post(
            "/api/v1/hosts/adhoc",
            json={"hostname": "example.com", "user": "alice", "port": 2222},
        )
        assert resp.status_code == 200
        assert resp.json()["alias"] == "adhoc:alice@example.com:2222"

    def test_add_invalid_hostname_422(self, auth_client):
        resp = auth_client.post(
            "/api/v1/hosts/adhoc",
            json={"hostname": "bad host name"},
        )
        assert resp.status_code == 422

    def test_add_bad_port_422(self, auth_client):
        resp = auth_client.post(
            "/api/v1/hosts/adhoc",
            json={"hostname": "example.com", "port": 99999},
        )
        assert resp.status_code == 422

    def test_adhoc_appears_in_host_list(self, auth_client):
        auth_client.post("/api/v1/hosts/adhoc", json={"hostname": "example.com"})
        resp = auth_client.get("/api/v1/hosts")
        aliases = [h["host"] for h in resp.json()["hosts"]]
        assert "adhoc:example.com" in aliases


class TestAuthTaxonomy:
    def test_auth_config_alias_accepted(self, auth_client, monkeypatch):
        """Config aliases can also authenticate with a password (key auth
        may fail for them too — same as `ssh wb` prompting in a terminal)."""
        from src.ssh.connection_pool import ConnectionPool

        async def ok_connect(self, host):
            return MagicMock()

        monkeypatch.setattr(ConnectionPool, "get_connection", ok_connect)
        resp = auth_client.post(
            "/api/v1/hosts/my-server/auth",
            json={"password": "x"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "connected"

    def test_auth_invalid_password_401(self, auth_client, monkeypatch):
        """Simulate a wrong-password ConnectionError from the pool."""
        from src.ssh.connection_pool import ConnectionPool

        async def fail_connect(self, host):
            raise ConnectionError("Cannot connect: Permission denied (password)")

        monkeypatch.setattr(ConnectionPool, "get_connection", fail_connect)
        auth_client.post("/api/v1/hosts/adhoc", json={"hostname": "example.com"})
        resp = auth_client.post(
            "/api/v1/hosts/adhoc:example.com/auth",
            json={"password": "wrong"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "invalid_password"

    def test_auth_host_key_unknown_419(self, auth_client, monkeypatch):
        from src.ssh.connection_pool import ConnectionPool
        from src.ssh.host_keys import HostKeyError

        async def fail_connect(self, host):
            raise HostKeyError("host_key_unknown", "SHA256:abc123")

        monkeypatch.setattr(ConnectionPool, "get_connection", fail_connect)
        auth_client.post("/api/v1/hosts/adhoc", json={"hostname": "example.com"})
        resp = auth_client.post(
            "/api/v1/hosts/adhoc:example.com/auth",
            json={"password": "x"},
        )
        assert resp.status_code == 419
        assert resp.json()["detail"] == "host_key_unknown"
        assert resp.headers["x-fingerprint"] == "SHA256:abc123"

    def test_auth_rate_limit_423(self, auth_client, monkeypatch):
        from src.api import hosts as hosts_mod
        from src.ssh.connection_pool import ConnectionPool

        async def fail_connect(self, host):
            raise ConnectionError("Cannot connect: auth failed")

        monkeypatch.setattr(ConnectionPool, "get_connection", fail_connect)
        auth_client.post("/api/v1/hosts/adhoc", json={"hostname": "example.com"})
        for _ in range(5):
            auth_client.post(
                "/api/v1/hosts/adhoc:example.com/auth",
                json={"password": "wrong"},
            )
        resp = auth_client.post(
            "/api/v1/hosts/adhoc:example.com/auth",
            json={"password": "wrong"},
        )
        assert resp.status_code == 423
        assert "retry-after" in {k.lower() for k in resp.headers}


class TestTrust:
    def test_trust_writes_key(self, auth_client, tmp_path):
        from unittest.mock import MagicMock
        from src.ssh.config_parser import SSHConfigParser
        from src.ssh.connection_pool import ConnectionPool

        # Point the app's pool at a tmp config dir — via the dependency
        # override (endpoints use Depends(get_pool), not app.state)
        app_pool = ConnectionPool(
            config_parser=MagicMock(spec=SSHConfigParser),
            config_dir=tmp_path,
        )
        from src.api.hosts import get_pool
        auth_client.app.dependency_overrides[get_pool] = lambda: app_pool
        auth_client.post("/api/v1/hosts/adhoc", json={"hostname": "example.com"})
        resp = auth_client.post(
            "/api/v1/hosts/adhoc:example.com/trust",
            json={"key_data": "ssh-ed25519 AAAAkey"},
        )
        assert resp.status_code == 200
        assert "example.com" in (tmp_path / "known_hosts").read_text()
