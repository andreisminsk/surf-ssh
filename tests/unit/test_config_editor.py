"""Unit tests for ConfigEditor — marker-delimited config append/remove."""

import sys

import pytest

from src.ssh.config_editor import ConfigEditor, ConfigEditorError


@pytest.fixture
def editor(tmp_path):
    return ConfigEditor(config_path=tmp_path / "config")


@pytest.fixture
def editor_with_content(editor, tmp_path):
    """Pre-existing user content in the config file."""
    (tmp_path / "config").write_text(
        "Host gcp\n"
        "    HostName 34.26.244.116\n"
        "    User alice\n"
        "\n"
        "Host wb\n"
        "    HostName 181.215.15.139\n"
        "    User bob\n"
    )
    return editor


class TestAppend:
    def test_creates_file_if_missing(self, editor, tmp_path):
        editor.append_entry("test-host", {"hostname": "10.0.0.5", "user": "root"}, tag="ephemeral")
        content = (tmp_path / "config").read_text()
        assert "Host test-host" in content
        assert "10.0.0.5" in content

    def test_preserves_existing_content(self, editor_with_content, tmp_path):
        editor_with_content.append_entry("new-host", {"hostname": "10.0.0.5"}, tag="ephemeral")
        content = (tmp_path / "config").read_text()
        assert "Host gcp" in content
        assert "Host wb" in content
        assert "Host new-host" in content

    def test_markers_present(self, editor, tmp_path):
        editor.append_entry("test-host", {"hostname": "10.0.0.5"}, tag="ephemeral")
        content = (tmp_path / "config").read_text()
        assert "# surf-ssh:test-host:ephemeral" in content
        assert "# end:surf-ssh:test-host" in content

    def test_persistent_tag(self, editor, tmp_path):
        editor.append_entry("saved", {"hostname": "10.0.0.5"}, tag="persistent")
        content = (tmp_path / "config").read_text()
        assert "# surf-ssh:saved:persistent" in content

    @pytest.mark.skipif(sys.platform == "win32", reason="0600 permission bits are POSIX-only (no-op on NTFS)")
    def test_file_permissions(self, editor, tmp_path):
        editor.append_entry("test", {"hostname": "10.0.0.5"}, tag="ephemeral")
        mode = (tmp_path / "config").stat().st_mode & 0o777
        assert mode == 0o600

    @pytest.mark.skipif(sys.platform == "win32", reason="0600 permission bits are POSIX-only (no-op on NTFS)")
    def test_preserves_existing_permissions(self, editor_with_content, tmp_path):
        import os
        os.chmod(tmp_path / "config", 0o644)
        editor_with_content.append_entry("new", {"hostname": "10.0.0.5"}, tag="ephemeral")
        mode = (tmp_path / "config").stat().st_mode & 0o777
        assert mode == 0o644

    def test_full_block_format(self, editor, tmp_path):
        editor.append_entry("test", {
            "hostname": "10.0.0.5", "user": "root", "port": 2222,
            "identityfile": "~/.ssh/key", "proxyjump": "bastion",
            "options": ["ServerAliveInterval=30"],
        }, tag="ephemeral")
        content = (tmp_path / "config").read_text()
        assert "HostName 10.0.0.5" in content
        assert "User root" in content
        assert "Port 2222" in content
        assert "IdentityFile ~/.ssh/key" in content
        assert "ProxyJump bastion" in content
        assert "ServerAliveInterval=30" in content

    def test_default_port_omitted(self, editor, tmp_path):
        editor.append_entry("test", {"hostname": "10.0.0.5", "port": 22}, tag="ephemeral")
        content = (tmp_path / "config").read_text()
        assert "Port" not in content


class TestRemove:
    def test_remove_existing(self, editor, tmp_path):
        editor.append_entry("test", {"hostname": "10.0.0.5"}, tag="ephemeral")
        assert editor.remove_entry("test") is True
        content = (tmp_path / "config").read_text()
        assert "test" not in content

    def test_remove_nonexistent(self, editor):
        assert editor.remove_entry("nope") is False

    def test_remove_preserves_other_entries(self, editor_with_content, tmp_path):
        editor_with_content.append_entry("ephemeral-1", {"hostname": "10.0.0.5"}, tag="ephemeral")
        editor_with_content.append_entry("ephemeral-2", {"hostname": "10.0.0.6"}, tag="ephemeral")
        editor_with_content.remove_entry("ephemeral-1")
        content = (tmp_path / "config").read_text()
        assert "ephemeral-1" not in content
        assert "ephemeral-2" in content
        assert "Host gcp" in content
        assert "Host wb" in content

    def test_remove_preserves_user_content(self, editor_with_content, tmp_path):
        editor_with_content.append_entry("temp", {"hostname": "10.0.0.5"}, tag="ephemeral")
        editor_with_content.remove_entry("temp")
        content = (tmp_path / "config").read_text()
        assert "Host gcp" in content
        assert "HostName 34.26.244.116" in content
        assert "Host wb" in content


class TestRemoveAllEphemeral:
    def test_removes_only_ephemeral(self, editor_with_content, tmp_path):
        editor_with_content.append_entry("ephemeral-1", {"hostname": "10.0.0.5"}, tag="ephemeral")
        editor_with_content.append_entry("saved-1", {"hostname": "10.0.0.6"}, tag="persistent")
        count = editor_with_content.remove_all_ephemeral()
        assert count == 1
        content = (tmp_path / "config").read_text()
        assert "ephemeral-1" not in content
        assert "saved-1" in content

    def test_removes_multiple(self, editor, tmp_path):
        editor.append_entry("e1", {"hostname": "10.0.0.5"}, tag="ephemeral")
        editor.append_entry("e2", {"hostname": "10.0.0.6"}, tag="ephemeral")
        editor.append_entry("p1", {"hostname": "10.0.0.7"}, tag="persistent")
        count = editor.remove_all_ephemeral()
        assert count == 2
        content = (tmp_path / "config").read_text()
        assert "e1" not in content
        assert "e2" not in content
        assert "p1" in content

    def test_empty_file(self, editor):
        assert editor.remove_all_ephemeral() == 0

    def test_preserves_user_content(self, editor_with_content, tmp_path):
        editor_with_content.append_entry("temp", {"hostname": "10.0.0.5"}, tag="ephemeral")
        editor_with_content.remove_all_ephemeral()
        content = (tmp_path / "config").read_text()
        assert "Host gcp" in content
        assert "Host wb" in content
