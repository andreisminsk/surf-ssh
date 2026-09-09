"""Path traversal prevention — the primary security boundary."""

from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import unquote

import asyncssh


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


async def resolve_and_validate(sftp, host: str, raw_path: str) -> str:
    """Validate a path, resolve remote symlinks, then re-validate the result.

    The string-level check in validate_path() cannot see through symlinks
    on the remote host. sftp.realpath() canonicalizes the path server-side;
    re-validating the result guarantees no '..' components survive
    symlink resolution.

    If realpath fails (broken symlink, connection issue), fall back to the
    string-validated path — the subsequent SFTP operation will fail with
    an appropriate error, and the fallback path is '..'-free by construction.
    """
    validated = validate_path(raw_path)
    try:
        resolved = await sftp.realpath(host, validated)
    except (asyncssh.Error, ConnectionError, OSError):
        return validated
    try:
        return validate_path(resolved)
    except PathValidationError:
        raise PathValidationError(
            f"Symlink traversal detected: {raw_path} -> {resolved}"
        ) from None
