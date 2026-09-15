"""Unit tests for CmdParser — ssh/scp command string parsing."""

import pytest

from src.ssh.cmd_parser import CmdParseError, parse_ssh_command


class TestBasicSsh:
    def test_user_at_host(self):
        r = parse_ssh_command("ssh root@10.0.0.5")
        assert r["hostname"] == "10.0.0.5"
        assert r["user"] == "root"

    def test_bare_host(self):
        r = parse_ssh_command("ssh my-server")
        assert r["hostname"] == "my-server"
        assert r["user"] == ""

    def test_with_port(self):
        r = parse_ssh_command("ssh -p 2222 root@10.0.0.5")
        assert r["port"] == 2222
        assert r["hostname"] == "10.0.0.5"

    def test_with_identity(self):
        r = parse_ssh_command("ssh -i ~/.ssh/key root@10.0.0.5")
        assert r["identityfile"].endswith("key")
        assert r["user"] == "root"

    def test_with_l_flag(self):
        r = parse_ssh_command("ssh -l alice 10.0.0.5")
        assert r["user"] == "alice"
        assert r["hostname"] == "10.0.0.5"

    def test_l_overrides_user_at(self):
        r = parse_ssh_command("ssh -l alice bob@10.0.0.5")
        assert r["user"] == "alice"

    def test_proxyjump(self):
        r = parse_ssh_command("ssh -J jump@bastion 10.0.0.5")
        assert r["proxyjump"] == "jump@bastion"

    def test_o_options(self):
        r = parse_ssh_command("ssh -o StrictHostKeyChecking=no root@10.0.0.5")
        assert "StrictHostKeyChecking=no" in r["options"]

    def test_multiple_o(self):
        r = parse_ssh_command("ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 root@10.0.0.5")
        assert len(r["options"]) == 2

    def test_full_command(self):
        r = parse_ssh_command("ssh -i ~/.ssh/key -p 2222 -J bastion root@10.0.0.5 -o ServerAliveInterval=30")
        assert r["hostname"] == "10.0.0.5"
        assert r["user"] == "root"
        assert r["port"] == 2222
        assert r["identityfile"].endswith("key")
        assert r["proxyjump"] == "bastion"
        assert "ServerAliveInterval=30" in r["options"]


class TestScp:
    def test_scp_basic(self):
        r = parse_ssh_command("scp file.txt root@10.0.0.5:/tmp/")
        assert r["hostname"] == "10.0.0.5"
        assert r["user"] == "root"

    def test_scp_capital_p(self):
        r = parse_ssh_command("scp -P 2222 file.txt root@10.0.0.5:/tmp/")
        assert r["port"] == 2222

    def test_scp_identity(self):
        r = parse_ssh_command("scp -i ~/.ssh/key file.txt root@10.0.0.5:/tmp/")
        assert r["identityfile"].endswith("key")

    def test_scp_from_remote(self):
        r = parse_ssh_command("scp root@10.0.0.5:/tmp/file.txt .")
        assert r["hostname"] == "10.0.0.5"
        assert r["user"] == "root"


class TestErrors:
    def test_empty_command(self):
        with pytest.raises(CmdParseError):
            parse_ssh_command("")

    def test_not_ssh(self):
        with pytest.raises(CmdParseError, match="Expected"):
            parse_ssh_command("telnet 10.0.0.5")

    def test_no_host(self):
        with pytest.raises(CmdParseError, match="No host"):
            parse_ssh_command("ssh -p 22")

    def test_bad_port(self):
        with pytest.raises(CmdParseError, match="port"):
            parse_ssh_command("ssh -p 99999 root@10.0.0.5")


class TestEdgeCases:
    def test_quoted_command(self):
        r = parse_ssh_command('ssh -o "ProxyCommand=ssh -W %h:%p jump" root@10.0.0.5')
        assert r["hostname"] == "10.0.0.5"
        assert any("ProxyCommand" in o for o in r["options"])

    def test_unknown_flag_skipped(self):
        r = parse_ssh_command("ssh -v root@10.0.0.5")
        assert r["hostname"] == "10.0.0.5"

    def test_identity_expansion(self):
        r = parse_ssh_command("ssh -i ~/custom/key root@10.0.0.5")
        assert "/custom/key" in r["identityfile"]
