"""TOTP 2FA for daemon mode — browser-direct authentication path.

Design: dev-docs/2FA-ARCH.md. The control socket (surf-ssh url) provides
kernel-enforced same-user auth; TOTP adds an equivalent-trust browser
path: a valid 6-digit code proves possession of the authenticator secret,
which no other local process has.

Storage: ~/.surf-ssh/totp.json (0600, plaintext secret — an attacker who
can read it can already read ~/.ssh/id_*; see arch doc §4).
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from pathlib import Path

import pyotp

# ── Rate limiting (the critical detail — see arch doc §8) ─────────
MAX_ATTEMPTS = 5          # attempts per window before lockout
WINDOW_SECONDS = 60.0     # sliding window
LOCKOUT_BASE = 60.0       # first lockout: 60s, then doubles
LOCKOUT_CAP = 30 * 60.0   # max lockout: 30 minutes


class TotpManager:
    """Manages the TOTP secret, verification, backup codes, and rate limiting."""

    def __init__(self, config_dir: Path) -> None:
        self._config_dir = config_dir
        self._totp_file = config_dir / "totp.json"
        # In-memory rate limiter state (resets on restart — acceptable,
        # restart requires local access, same trust as the socket path)
        self._attempts: list[float] = []
        self._lockout_until: float = 0.0
        self._lockout_level = 0

    # ── Secret management ──────────────────────────────────────────

    def is_enabled(self) -> bool:
        data = self._load()
        return bool(data and data.get("enabled"))

    def _load(self) -> dict | None:
        try:
            return json.loads(self._totp_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _save(self, data: dict) -> None:
        self._config_dir.mkdir(parents=True, exist_ok=True)
        self._totp_file.write_text(json.dumps(data), encoding="utf-8")
        os.chmod(self._totp_file, 0o600)

    def setup(self) -> tuple[str, str, list[str]]:
        """Generate a new secret + backup codes. Returns (secret, otpauth_uri, backup_codes).

        NOT persisted until confirm_setup() succeeds — prevents locking
        the user out with an unscanned secret.
        """
        secret = pyotp.random_base32()
        uri = pyotp.totp.TOTP(secret).provisioning_uri(
            name="surf-ssh", issuer_name="surf-ssh"
        )
        backup_codes = self._generate_backup_codes()
        return secret, uri, backup_codes

    def confirm_setup(self, secret: str, code: str, backup_codes: list[str]) -> bool:
        """Verify the first code against the secret, then persist."""
        if not pyotp.TOTP(secret).verify(code, valid_window=1):
            return False
        self._save({
            "secret": secret,
            "enabled": True,
            "backup_codes": [self._hash_code(self._normalize(c)) for c in backup_codes],
        })
        return True

    def disable(self) -> None:
        """Remove the TOTP configuration entirely (escape hatch)."""
        try:
            self._totp_file.unlink()
        except OSError:
            pass

    # ── Verification ───────────────────────────────────────────────

    def verify(self, code: str) -> bool:
        """Verify a TOTP code (rate-limited). Returns True on success."""
        if not self._check_rate_limit():
            raise RateLimitError(self._retry_after())
        ok = self._verify_code(code)
        self._record_attempt(ok)
        return ok

    def _verify_code(self, code: str) -> bool:
        data = self._load()
        if not data or not data.get("enabled"):
            return False
        code = code.strip().replace(" ", "")
        if not code.isdigit() or len(code) != 6:
            return False
        return pyotp.TOTP(data["secret"]).verify(code, valid_window=1)

    def verify_backup_code(self, code: str) -> bool:
        """Verify and consume a backup code (rate-limited)."""
        if not self._check_rate_limit():
            raise RateLimitError(self._retry_after())
        ok = self._verify_and_consume_backup(code)
        self._record_attempt(ok)
        return ok

    def _verify_and_consume_backup(self, code: str) -> bool:
        data = self._load()
        if not data or not data.get("enabled"):
            return False
        hashed = self._hash_code(self._normalize(code))
        remaining = [h for h in data.get("backup_codes", []) if h != hashed]
        if len(remaining) == len(data.get("backup_codes", [])):
            return False  # no match
        data["backup_codes"] = remaining
        self._save(data)
        return True

    # ── Rate limiting ──────────────────────────────────────────────

    def _check_rate_limit(self) -> bool:
        """True if a verify attempt is allowed now."""
        now = time.monotonic()
        if now < self._lockout_until:
            return False
        # Drop attempts outside the window
        self._attempts = [t for t in self._attempts if now - t < WINDOW_SECONDS]
        return len(self._attempts) < MAX_ATTEMPTS

    def _record_attempt(self, success: bool) -> None:
        now = time.monotonic()
        if success:
            # Successful auth resets the limiter
            self._attempts.clear()
            self._lockout_until = 0.0
            self._lockout_level = 0
            return
        self._attempts.append(now)
        if len(self._attempts) >= MAX_ATTEMPTS:
            # Exponential lockout: 60s, 120s, 240s, ... capped at 30min
            self._lockout_level += 1
            self._lockout_until = now + min(
                LOCKOUT_BASE * (2 ** (self._lockout_level - 1)), LOCKOUT_CAP
            )
            self._attempts.clear()

    def _retry_after(self) -> int:
        return max(1, int(self._lockout_until - time.monotonic()))

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _normalize(code: str) -> str:
        """Canonical form of a backup code: trimmed, no spaces, uppercase."""
        return code.strip().replace(" ", "").upper()

    @staticmethod
    def _hash_code(code: str) -> str:
        return hashlib.sha256(code.encode("utf-8")).hexdigest()

    @staticmethod
    def _generate_backup_codes(count: int = 10) -> list[str]:
        """Generate N single-use backup codes in xxxx-xxxx format."""
        codes = []
        for _ in range(count):
            raw = secrets.token_hex(4)  # 8 hex chars
            codes.append(f"{raw[:4]}-{raw[4:]}")
        return codes


class RateLimitError(Exception):
    """Raised when the verify endpoint is rate-limited (locked out)."""

    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__(f"Rate limited — retry after {retry_after}s")
