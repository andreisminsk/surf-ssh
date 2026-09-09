"""Unit tests for the ad-hoc host registry and pool password path."""

import pytest

from src.ssh.adhoc import AdHocHostError, AdHocHostRegistry


class TestMakeAlias:
    def test_basic(self):
        assert AdHocHostRegistry.make_alias("alice", "example.com", 22) == "adhoc:alice@example.com"

    def test_custom_port(self):
        assert AdHocHostRegistry.make_alias("alice", "example.com", 2222) == "adhoc:alice@example.com:2222"

    def test_no_user(self):
        assert AdHocHostRegistry.make_alias(None, "example.com", 22) == "adhoc:example.com"

    def test_ip_hostname(self):
        assert AdHocHostRegistry.make_alias(None, "192.168.1.10", 22) == "adhoc:192.168.1.10"

    def test_rejects_slash(self):
        with pytest.raises(AdHocHostError):
            AdHocHostRegistry.make_alias("u", "host/../../etc", 22)

    def test_rejects_whitespace(self):
        with pytest.raises(AdHocHostError):
            AdHocHostRegistry.make_alias("u", "my host", 22)

    def test_rejects_empty_hostname(self):
        with pytest.raises(AdHocHostError):
            AdHocHostRegistry.make_alias("u", "", 22)

    def test_rejects_bad_port(self):
        with pytest.raises(AdHocHostError):
            AdHocHostRegistry.make_alias("u", "host", 0)
        with pytest.raises(AdHocHostError):
            AdHocHostRegistry.make_alias("u", "host", 70000)

    def test_rejects_user_with_space(self):
        with pytest.raises(AdHocHostError):
            AdHocHostRegistry.make_alias("bad user", "host", 22)


class TestRegistry:
    def test_register_and_resolve(self):
        reg = AdHocHostRegistry()
        alias = reg.register("example.com", "alice", 2222)
        host = reg.resolve(alias)
        assert host is not None
        assert (host.hostname, host.user, host.port) == ("example.com", "alice", 2222)

    def test_idempotent(self):
        reg = AdHocHostRegistry()
        a1 = reg.register("example.com", "alice", 22)
        a2 = reg.register("example.com", "alice", 22)
        assert a1 == a2
        assert reg.list_hosts() == [a1]

    def test_resolve_config_alias_returns_none(self):
        reg = AdHocHostRegistry()
        assert reg.resolve("my-server") is None
        assert reg.resolve("adhoc:not-registered") is None

    def test_list_and_clear(self):
        reg = AdHocHostRegistry()
        reg.register("a.com", None, 22)
        reg.register("b.com", "bob", 22)
        assert len(reg.list_hosts()) == 2
        reg.clear()
        assert reg.list_hosts() == []


class TestPoolPasswordPath:
    def test_password_set_clear(self):
        from unittest.mock import MagicMock
        from src.ssh.config_parser import SSHConfigParser
        from src.ssh.connection_pool import ConnectionPool

        pool = ConnectionPool(config_parser=MagicMock(spec=SSHConfigParser))
        pool.set_password("adhoc:alice@example.com", "secret")
        assert pool._passwords["adhoc:alice@example.com"] == "secret"
        pool.clear_password("adhoc:alice@example.com")
        assert "adhoc:alice@example.com" not in pool._passwords

    def test_register_adhoc(self):
        from unittest.mock import MagicMock
        from src.ssh.config_parser import SSHConfigParser
        from src.ssh.connection_pool import ConnectionPool

        pool = ConnectionPool(config_parser=MagicMock(spec=SSHConfigParser))
        alias = pool.register_adhoc("example.com", "alice", 2222)
        assert alias == "adhoc:alice@example.com:2222"
        assert pool.is_adhoc(alias) is True
        assert pool.is_adhoc("my-server") is False
        assert pool.list_adhoc_hosts() == [alias]

    async def test_config_alias_connect_passes_password(self):
        """Config hosts whose key auth failed must connect with the stored
        password — the password kwarg has to reach asyncssh.connect."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from src.ssh.config_parser import SSHConfigParser
        from src.ssh.connection_pool import ConnectionPool

        parser = MagicMock(spec=SSHConfigParser)
        parser.get_host_config.return_value = {
            "hostname": "181.215.15.139", "port": 22, "proxyjump": "",
        }
        pool = ConnectionPool(config_parser=parser)
        pool.set_password("wb", "secret")

        with patch(
            "src.ssh.connection_pool.asyncssh.connect", new_callable=AsyncMock
        ) as mock_connect:
            await pool.get_connection("wb")
            assert mock_connect.call_args.kwargs.get("password") == "secret"

    async def test_config_alias_without_password_passes_none(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from src.ssh.config_parser import SSHConfigParser
        from src.ssh.connection_pool import ConnectionPool

        parser = MagicMock(spec=SSHConfigParser)
        parser.get_host_config.return_value = {
            "hostname": "example.com", "port": 22, "proxyjump": "",
        }
        pool = ConnectionPool(config_parser=parser)

        with patch(
            "src.ssh.connection_pool.asyncssh.connect", new_callable=AsyncMock
        ) as mock_connect:
            await pool.get_connection("my-server")
            assert mock_connect.call_args.kwargs.get("password") is None

    async def test_auth_failure_negative_cache(self):
        """After key auth fails once, concurrent requests must fast-fail
        with PasswordRequiredError — no repeated SSH handshakes against
        the remote (fail2ban / account lockout risk)."""
        import asyncssh
        from unittest.mock import AsyncMock, MagicMock, patch
        from src.ssh.config_parser import SSHConfigParser
        from src.ssh.connection_pool import ConnectionPool, PasswordRequiredError

        parser = MagicMock(spec=SSHConfigParser)
        parser.get_host_config.return_value = {
            "hostname": "example.com", "port": 22, "proxyjump": "",
        }
        pool = ConnectionPool(config_parser=parser)

        with patch(
            "src.ssh.connection_pool.asyncssh.connect",
            new_callable=AsyncMock,
            side_effect=asyncssh.PermissionDenied("Permission denied"),
        ) as mock_connect:
            with pytest.raises(PasswordRequiredError):
                await pool.get_connection("wb")
            # Concurrent request: fast-fail, no new handshake
            with pytest.raises(PasswordRequiredError):
                await pool.get_connection("wb")
            assert mock_connect.call_count == 1

        # Providing a password clears the flag → next connect proceeds
        pool.set_password("wb", "secret")
        assert "wb" not in pool._auth_failed_hosts

    async def test_close_all_clears_passwords(self):
        from unittest.mock import MagicMock
        from src.ssh.config_parser import SSHConfigParser
        from src.ssh.connection_pool import ConnectionPool

        pool = ConnectionPool(config_parser=MagicMock(spec=SSHConfigParser))
        pool.set_password("adhoc:alice@example.com", "secret")
        pool.register_adhoc("example.com", "alice", 22)
        await pool.close_all()
        # Hard invariant #3: zero password copies survive shutdown
        assert pool._passwords == {}
        assert pool.list_adhoc_hosts() == []
