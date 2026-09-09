"""Unit tests for TotpManager — TOTP 2FA core logic."""

import json
import time

import pyotp
import pytest

from src.security.totp import MAX_ATTEMPTS, RateLimitError, TotpManager


@pytest.fixture
def manager(tmp_path):
    return TotpManager(tmp_path)


@pytest.fixture
def enrolled(manager):
    """Enroll with a known secret, return (manager, totp)."""
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    ok = manager.confirm_setup(secret, totp.now(), ["aaaa-bbbb", "cccc-dddd"])
    assert ok is True
    return manager, totp


class TestSetup:
    def test_setup_generates_secret_uri_and_codes(self, manager):
        secret, uri, codes = manager.setup()
        assert len(secret) >= 32
        assert uri.startswith("otpauth://totp/")
        assert len(codes) == 10
        assert all(len(c) == 9 and c[4] == "-" for c in codes)

    def test_confirm_setup_persists(self, manager, tmp_path):
        secret = pyotp.random_base32()
        ok = manager.confirm_setup(secret, pyotp.TOTP(secret).now(), ["aaaa-bbbb"])
        assert ok is True
        assert manager.is_enabled() is True
        # File is 0600
        f = tmp_path / "totp.json"
        assert f.stat().st_mode & 0o777 == 0o600
        # Backup codes are hashed, not plaintext
        data = json.loads(f.read_text())
        assert "aaaa-bbbb" not in json.dumps(data)
        assert len(data["backup_codes"]) == 1

    def test_confirm_setup_rejects_wrong_code(self, manager):
        secret = pyotp.random_base32()
        ok = manager.confirm_setup(secret, "000000", ["aaaa-bbbb"])
        assert ok is False
        assert manager.is_enabled() is False

    def test_disable(self, enrolled, tmp_path):
        manager, _ = enrolled
        manager.disable()
        assert manager.is_enabled() is False
        assert not (tmp_path / "totp.json").exists()


class TestVerify:
    def test_valid_code_accepted(self, enrolled):
        manager, totp = enrolled
        assert manager.verify(totp.now()) is True

    def test_wrong_code_rejected(self, enrolled):
        manager, _ = enrolled
        assert manager.verify("000000") is False

    def test_code_with_spaces_normalized(self, enrolled):
        manager, totp = enrolled
        assert manager.verify(f" {totp.now()[:3]} {totp.now()[3:]} ") is True

    def test_non_numeric_rejected(self, enrolled):
        manager, _ = enrolled
        assert manager.verify("abcdef") is False

    def test_verify_when_not_enabled(self, manager):
        assert manager.verify("123456") is False


class TestBackupCodes:
    def test_valid_backup_code_consumed(self, enrolled):
        manager, _ = enrolled
        assert manager.verify_backup_code("aaaa-bbbb") is True
        # Single-use: second attempt fails
        assert manager.verify_backup_code("aaaa-bbbb") is False

    def test_second_backup_code_still_valid(self, enrolled):
        manager, _ = enrolled
        assert manager.verify_backup_code("aaaa-bbbb") is True
        assert manager.verify_backup_code("cccc-dddd") is True

    def test_unknown_backup_code_rejected(self, enrolled):
        manager, _ = enrolled
        assert manager.verify_backup_code("zzzz-zzzz") is False


class TestRateLimit:
    def test_lockout_after_max_attempts(self, enrolled):
        manager, _ = enrolled
        for _ in range(MAX_ATTEMPTS):
            assert manager.verify("000000") is False
        with pytest.raises(RateLimitError) as exc:
            manager.verify("000000")
        assert exc.value.retry_after >= 55  # ~60s first lockout

    def test_success_resets_limiter(self, enrolled):
        manager, totp = enrolled
        for _ in range(MAX_ATTEMPTS - 1):
            manager.verify("000000")
        assert manager.verify(totp.now()) is True
        # Limiter reset — fresh budget of attempts
        for _ in range(MAX_ATTEMPTS - 1):
            assert manager.verify("000000") is False
        assert manager.verify(totp.now()) is True

    def test_exponential_lockout(self, enrolled):
        manager, _ = enrolled
        lockouts = []
        for _ in range(3):
            # Burn the full attempt budget (each failure appends; the 5th
            # triggers lockout and clears the list)
            while True:
                try:
                    manager.verify("000000")
                except RateLimitError as e:
                    lockouts.append(e.retry_after)
                    break
            # Simulate lockout expiry
            manager._lockout_until = 0.0
        # Each successive lockout is longer (60, 120, 240)
        assert len(lockouts) == 3
        assert lockouts[1] > lockouts[0]
        assert lockouts[2] > lockouts[1]
