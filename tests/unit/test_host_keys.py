"""Unit tests for HostKeyTrust — TOFU host key management."""

import os

import pytest

from src.ssh.host_keys import HostKeyError, HostKeyTrust


@pytest.fixture
def trust(tmp_path):
    return HostKeyTrust(tmp_path)


class TestTrustedKeys:
    def test_empty_file_returns_empty_known_hosts(self, trust, tmp_path):
        import asyncssh
        (tmp_path / "known_hosts").write_text("")
        keys = trust.trusted_keys()
        assert isinstance(keys, asyncssh.SSHKnownHosts)

    def test_trusted_key_roundtrip(self, trust, tmp_path):
        import asyncssh
        # A real OpenSSH-format key line (ed25519 public key)
        key_line = "example.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB1234567890abc"
        trust.trust("example.com", 22, key_line.encode())
        keys = trust.trusted_keys()
        assert isinstance(keys, asyncssh.SSHKnownHosts)

    def test_custom_port_marker(self, trust, tmp_path):
        key_line = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB1234567890abc"
        trust.trust("example.com", 2222, key_line.encode())
        content = (tmp_path / "known_hosts").read_text()
        assert "[example.com]:2222" in content

    def test_default_port_no_marker(self, trust, tmp_path):
        key_line = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB1234567890abc"
        trust.trust("example.com", 22, key_line.encode())
        content = (tmp_path / "known_hosts").read_text()
        assert content.startswith("example.com ")

    def test_file_permissions_0600(self, trust, tmp_path):
        key_line = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB1234567890abc"
        trust.trust("example.com", 22, key_line.encode())
        mode = (tmp_path / "known_hosts").stat().st_mode & 0o777
        assert mode == 0o600

    def test_missing_file_returns_none(self, trust, tmp_path):
        assert trust.trusted_keys() is None

    def test_corrupt_file_tolerated(self, trust, tmp_path):
        # Garbage lines are silently skipped by import_known_hosts —
        # behaves as "nothing trusted", never raises
        (tmp_path / "known_hosts").write_text("garbage not a key {{{")
        assert trust.trusted_keys() is not None  # empty SSHKnownHosts


class TestTranslateConnectError:
    def test_unknown_key(self, trust):
        import asyncssh
        exc = asyncssh.HostKeyNotVerifiable("No matching host key found for host example.com")
        result = trust.translate_connect_error(exc)
        assert isinstance(result, HostKeyError)
        assert result.kind == "host_key_unknown"

    def test_changed_key(self, trust):
        import asyncssh
        exc = asyncssh.HostKeyNotVerifiable("Host key does not match SHA256:abc123xyz")
        result = trust.translate_connect_error(exc)
        assert isinstance(result, HostKeyError)
        assert result.kind == "host_key_changed"
        assert result.fingerprint == "SHA256:abc123xyz"

    def test_non_hostkey_error_passthrough(self, trust):
        exc = ValueError("something else")
        assert trust.translate_connect_error(exc) is exc


class TestPoolTrust:
    async def test_trust_host_key_requires_adhoc(self):
        from unittest.mock import MagicMock
        from src.ssh.config_parser import SSHConfigParser
        from src.ssh.connection_pool import ConnectionPool

        pool = ConnectionPool(config_parser=MagicMock(spec=SSHConfigParser))
        with pytest.raises(ValueError, match="Not an ad-hoc host"):
            pool.trust_host_key("my-server", b"key")

    async def test_trust_host_key_writes_file(self, tmp_path):
        from unittest.mock import MagicMock
        from src.ssh.config_parser import SSHConfigParser
        from src.ssh.connection_pool import ConnectionPool

        pool = ConnectionPool(
            config_parser=MagicMock(spec=SSHConfigParser),
            config_dir=tmp_path,
        )
        pool.register_adhoc("example.com", "alice", 22)
        pool.trust_host_key("adhoc:alice@example.com", b"ssh-ed25519 AAAAkey")
        content = (tmp_path / "known_hosts").read_text()
        assert "example.com" in content
        assert "AAAAkey" in content
