"""Path traversal prevention — the primary security boundary."""

from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import unquote


class PathValidationError(Exception):
    """Raised when a path fails validation."""


def validate_path(raw_path: str) -> str:
    """
    Validate and normalize a remote file path.

    Rules:
    1. URL-decode the path.
    2. Reject paths containing '..' components.
    3. Normalize to a POSIX absolute path.

    Returns the validated absolute path string.
    """
    decoded = unquote(raw_path)
    posix_path = PurePosixPath(decoded)

    if ".." in posix_path.parts:
        raise PathValidationError(f"Path traversal detected: {decoded}")

    if not posix_path.is_absolute():
        # Relative paths are resolved from root
        posix_path = PurePosixPath("/") / posix_path

    return str(posix_path)
