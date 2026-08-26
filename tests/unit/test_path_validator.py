"""Unit tests for path_validator.py — the primary security boundary."""

import pytest
from src.security.path_validator import validate_path, PathValidationError


class TestValidPaths:
    """Tests for valid path inputs."""

    def test_simple_absolute_path(self):
        assert validate_path("/home/user/file.txt") == "/home/user/file.txt"

    def test_root_path(self):
        assert validate_path("/") == "/"

    def test_deep_nested_path(self):
        assert validate_path("/a/b/c/d/e/f") == "/a/b/c/d/e/f"

    def test_path_with_spaces(self):
        assert validate_path("/home/user/my files/doc.txt") == "/home/user/my files/doc.txt"

    def test_path_with_dots_in_filename(self):
        # Dots in filenames are fine, just not .. path components
        assert validate_path("/home/user/file.tar.gz") == "/home/user/file.tar.gz"

    def test_path_with_extension(self):
        assert validate_path("/var/log/syslog.1") == "/var/log/syslog.1"

    def test_hidden_file(self):
        assert validate_path("/home/user/.bashrc") == "/home/user/.bashrc"

    def test_hidden_directory(self):
        assert validate_path("/home/user/.config/app") == "/home/user/.config/app"


class TestUrlDecoding:
    """Tests for URL-encoded paths."""

    def test_encoded_slash(self):
        assert validate_path("/home/user%2Ffile.txt") == "/home/user/file.txt"

    def test_encoded_space(self):
        assert validate_path("/home/user%20dir/file.txt") == "/home/user dir/file.txt"

    def test_encoded_special_chars(self):
        assert validate_path("/home/user%40test/file.txt") == "/home/user@test/file.txt"

    def test_double_encoded(self):
        # %2525 should decode to %25, not double-decode
        result = validate_path("/home/user%2525file")
        assert "%25" in result or "%2" in result  # single decode only


class TestRelativePaths:
    """Tests for relative path normalization."""

    def test_relative_path_becomes_absolute(self):
        assert validate_path("relative/path") == "/relative/path"

    def test_single_dot(self):
        # PurePosixPath collapses . components
        result = validate_path("/home/./user/file")
        assert result == "/home/user/file"

    def test_bare_filename(self):
        assert validate_path("file.txt") == "/file.txt"


class TestPathTraversal:
    """Tests for path traversal attack prevention."""

    @pytest.mark.parametrize("malicious_path", [
        "../etc/passwd",
        "/../etc/passwd",
        "/home/../../etc/passwd",
        "/a/b/../../../c",
        "/../../../etc/shadow",
        "/home/user/../../../root",
        "..%2f..%2fetc%2fpasswd",
        "/home/user/..%2F..%2Fetc",
    ])
    def test_rejects_traversal(self, malicious_path):
        with pytest.raises(PathValidationError, match="Path traversal detected"):
            validate_path(malicious_path)

    def test_traversal_error_message_contains_path(self):
        with pytest.raises(PathValidationError) as exc_info:
            validate_path("../etc/passwd")
        assert "../etc/passwd" in str(exc_info.value)


class TestEdgeCases:
    """Tests for edge cases."""

    def test_empty_path(self):
        result = validate_path("")
        assert result == "/"

    def test_single_slash(self):
        assert validate_path("/") == "/"

    def test_trailing_slash_preserved_by_posix(self):
        # PurePosixPath normalizes trailing slashes
        result = validate_path("/home/user/")
        assert result == "/home/user"

    def test_multiple_slashes_collapsed(self):
        result = validate_path("/home//user///file")
        assert result == "/home/user/file"

    def test_tilde_not_expanded(self):
        # We don't expand ~ — it's a remote path
        result = validate_path("/home/~user/file")
        assert "~user" in result
