"""Local session token management with cookie exchange.

Security properties:
- Filenames are sha256(token) — a directory listing never reveals tokens.
- Files are written with 0600 permissions (owner-only).
- Sessions expire after SESSION_TTL_SECONDS; expired and legacy files
  are purged on startup.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

SESSION_TTL_SECONDS = 24 * 3600  # sessions expire after 24 hours
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class SessionManager:
    """Manages session tokens stored on disk."""

    def __init__(self, sessions_dir: Path, ttl: float = SESSION_TTL_SECONDS) -> None:
        self._sessions_dir = sessions_dir
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._ttl = ttl
        self._purge_stale_files()

    def _session_path(self, token: str) -> Path:
        """Map a token to its on-disk path (sha256 of the token)."""
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return self._sessions_dir / f"{digest}.json"

    def _purge_stale_files(self) -> None:
        """Remove legacy plaintext-named files and expired sessions."""
        now = time.time()
        for file in self._sessions_dir.glob("*.json"):
            if not _HEX64.match(file.stem):
                # Legacy format: filename was the raw token — remove it.
                self._safe_unlink(file)
                continue
            try:
                data = json.loads(file.read_text())
                if now - data.get("created", 0) > self._ttl:
                    self._safe_unlink(file)
            except (json.JSONDecodeError, OSError):
                self._safe_unlink(file)

    @staticmethod
    def _safe_unlink(file: Path) -> None:
        try:
            file.unlink()
        except OSError:
            pass

    def create_session(self) -> str:
        """Create a new session token and persist it."""
        token = secrets.token_urlsafe(32)
        session_file = self._session_path(token)
        session_file.write_text(json.dumps({"created": time.time()}))
        os.chmod(session_file, 0o600)
        return token

    def validate_session(self, token: str) -> bool:
        """Check if a session token is valid and not expired."""
        if not token:
            return False
        session_file = self._session_path(token)
        if not session_file.exists():
            return False
        try:
            data = json.loads(session_file.read_text())
        except (json.JSONDecodeError, OSError):
            return False
        if time.time() - data.get("created", 0) > self._ttl:
            self.destroy_session(token)
            return False
        return True

    def destroy_session(self, token: str) -> None:
        """Remove a session token."""
        self._safe_unlink(self._session_path(token))
