"""Local session token management with cookie exchange."""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path


class SessionManager:
    """Manages session tokens stored on disk."""

    def __init__(self, sessions_dir: Path) -> None:
        self._sessions_dir = sessions_dir
        self._sessions_dir.mkdir(parents=True, exist_ok=True)

    def create_session(self) -> str:
        """Create a new session token and persist it."""
        token = secrets.token_urlsafe(32)
        session_data = {"token": token, "created": time.time()}
        session_file = self._sessions_dir / f"{token}.json"
        session_file.write_text(json.dumps(session_data))
        return token

    def validate_session(self, token: str) -> bool:
        """Check if a session token is valid."""
        if not token:
            return False
        session_file = self._sessions_dir / f"{token}.json"
        if not session_file.exists():
            return False
        try:
            data = json.loads(session_file.read_text())
            return data.get("token") == token
        except (json.JSONDecodeError, OSError):
            return False

    def destroy_session(self, token: str) -> None:
        """Remove a session token."""
        session_file = self._sessions_dir / f"{token}.json"
        if session_file.exists():
            session_file.unlink()
