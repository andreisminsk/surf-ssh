"""Unit tests for session_auth.py."""

import hashlib
import json
import time
from pathlib import Path

import pytest
from src.security.session_auth import SessionManager


@pytest.fixture
def session_manager(tmp_path):
    """Create a SessionManager with a temporary sessions directory."""
    return SessionManager(tmp_path / "sessions")


class TestCreateSession:
    """Tests for session creation."""

    def test_creates_valid_token(self, session_manager):
        token = session_manager.create_session()
        assert token is not None
        assert len(token) > 20  # token_urlsafe(32) produces ~43 chars

    def test_token_is_unique(self, session_manager):
        token1 = session_manager.create_session()
        token2 = session_manager.create_session()
        assert token1 != token2

    def test_persists_session_file(self, session_manager):
        token = session_manager.create_session()
        session_file = session_manager._session_path(token)
        assert session_file.exists()
        data = json.loads(session_file.read_text())
        assert "created" in data

    def test_filename_does_not_leak_token(self, session_manager):
        token = session_manager.create_session()
        session_file = session_manager._session_path(token)
        assert token not in session_file.name
        # No file in the directory contains the raw token in its name
        for f in session_manager._sessions_dir.iterdir():
            assert token not in f.name

    def test_session_file_is_owner_only(self, session_manager):
        token = session_manager.create_session()
        session_file = session_manager._session_path(token)
        mode = session_file.stat().st_mode & 0o777
        assert mode == 0o600

    def test_token_is_url_safe(self, session_manager):
        token = session_manager.create_session()
        # Should not contain characters that need URL encoding
        for char in token:
            assert char.isalnum() or char in "-_"


class TestValidateSession:
    """Tests for session validation."""

    def test_validates_existing_session(self, session_manager):
        token = session_manager.create_session()
        assert session_manager.validate_session(token) is True

    def test_rejects_invalid_token(self, session_manager):
        assert session_manager.validate_session("invalid-token") is False

    def test_rejects_empty_token(self, session_manager):
        assert session_manager.validate_session("") is False

    def test_rejects_none_token(self, session_manager):
        assert session_manager.validate_session(None) is False

    def test_rejects_destroyed_session(self, session_manager):
        token = session_manager.create_session()
        session_manager.destroy_session(token)
        assert session_manager.validate_session(token) is False

    def test_rejects_tampered_token(self, session_manager):
        token = session_manager.create_session()
        tampered = token[:-5] + "XXXXX"
        assert session_manager.validate_session(tampered) is False


class TestDestroySession:
    """Tests for session destruction."""

    def test_removes_session_file(self, session_manager):
        token = session_manager.create_session()
        session_file = session_manager._session_path(token)
        assert session_file.exists()
        session_manager.destroy_session(token)
        assert not session_file.exists()

    def test_destroy_nonexistent_session_no_error(self, session_manager):
        # Should not raise
        session_manager.destroy_session("nonexistent-token")

    def test_destroy_already_destroyed(self, session_manager):
        token = session_manager.create_session()
        session_manager.destroy_session(token)
        session_manager.destroy_session(token)  # Should not raise


class TestMultipleSessions:
    """Tests for multiple concurrent sessions."""

    def test_multiple_sessions_all_valid(self, session_manager):
        tokens = [session_manager.create_session() for _ in range(5)]
        for token in tokens:
            assert session_manager.validate_session(token) is True

    def test_destroy_one_does_not_affect_others(self, session_manager):
        tokens = [session_manager.create_session() for _ in range(3)]
        session_manager.destroy_session(tokens[1])
        assert session_manager.validate_session(tokens[0]) is True
        assert session_manager.validate_session(tokens[1]) is False
        assert session_manager.validate_session(tokens[2]) is True


class TestSessionExpiry:
    """Tests for TTL enforcement and startup purge."""

    def test_expired_session_rejected(self, tmp_path):
        mgr = SessionManager(tmp_path / "sessions", ttl=10.0)
        token = mgr.create_session()
        # Forge an expired timestamp
        session_file = mgr._session_path(token)
        data = json.loads(session_file.read_text())
        data["created"] = time.time() - 100.0
        session_file.write_text(json.dumps(data))
        assert mgr.validate_session(token) is False
        # Expired session is also removed from disk
        assert not session_file.exists()

    def test_valid_session_within_ttl(self, tmp_path):
        mgr = SessionManager(tmp_path / "sessions", ttl=3600.0)
        token = mgr.create_session()
        assert mgr.validate_session(token) is True

    def test_startup_purges_expired_files(self, tmp_path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir(parents=True)
        # An expired hashed session file
        expired = sessions_dir / (hashlib.sha256(b"old").hexdigest() + ".json")
        expired.write_text(json.dumps({"created": time.time() - 999999}))
        # A legacy plaintext-named file
        legacy = sessions_dir / "some-old-plaintext-token.json"
        legacy.write_text(json.dumps({"token": "some-old-plaintext-token", "created": time.time()}))
        # A fresh valid file
        fresh = sessions_dir / (hashlib.sha256(b"fresh").hexdigest() + ".json")
        fresh.write_text(json.dumps({"created": time.time()}))

        SessionManager(sessions_dir, ttl=3600.0)

        assert not expired.exists()
        assert not legacy.exists()
        assert fresh.exists()
