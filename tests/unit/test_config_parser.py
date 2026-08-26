"""Unit tests for SSH config parser."""

from pathlib import Path
from unittest.mock import patch

import pytest
from src.ssh.config_parser import SSHConfigParser


@pytest.fixture
def ssh_config(tmp_path):
    """Create a test SSH config file."""
    config_path = tmp_path / "config"
    config_path.write_text(
        """# Test SSH config
Host mac-remote
    HostName 192.168.1.100
    User admin
    Port 2222
    IdentityFile ~/.ssh/id_ed25519

Host linux-server
    HostName 10.0.0.50
    User root
    ProxyJump jump-host

Host jump-host
    HostName jump.example.com
    User jumpuser

Host *
    User defaultuser
"""
    )
    return config_path


@pytest.fixture
def parser(ssh_config):
    return SSHConfigParser(config_path=ssh_config)


class TestListHosts:
    """Tests for host listing."""

    def test_lists_all_hosts(self, parser):
        hosts = parser.list_hosts()
        assert "mac-remote" in hosts
        assert "linux-server" in hosts
        assert "jump-host" in hosts

    def test_excludes_wildcard_hosts(self, parser):
        hosts = parser.list_hosts()
        assert "*" not in hosts

    def test_empty_config(self, tmp_path):
        config_path = tmp_path / "empty_config"
        config_path.write_text("")
        parser = SSHConfigParser(config_path=config_path)
        assert parser.list_hosts() == []

    def test_missing_config_file(self, tmp_path):
        config_path = tmp_path / "nonexistent"
        parser = SSHConfigParser(config_path=config_path)
        assert parser.list_hosts() == []

    def test_comments_ignored(self, tmp_path):
        config_path = tmp_path / "config"
        config_path.write_text("# Just a comment\n# Another comment\n")
        parser = SSHConfigParser(config_path=config_path)
        assert parser.list_hosts() == []


class TestGetHostConfig:
    """Tests for host config resolution."""

    def test_gets_hostname(self, parser):
        config = parser.get_host_config("mac-remote")
        assert config["hostname"] == "192.168.1.100"

    def test_gets_user(self, parser):
        config = parser.get_host_config("mac-remote")
        assert config["user"] == "admin"

    def test_gets_port(self, parser):
        config = parser.get_host_config("mac-remote")
        assert config["port"] == 2222

    def test_gets_proxyjump(self, parser):
        config = parser.get_host_config("linux-server")
        assert config["proxyjump"] == "jump-host"

    def test_no_proxyjump_returns_empty(self, parser):
        config = parser.get_host_config("mac-remote")
        assert config["proxyjump"] == ""

    def test_unknown_host_falls_back(self, parser):
        config = parser.get_host_config("unknown-host")
        # Should still return something, possibly with defaults
        assert "hostname" in config
