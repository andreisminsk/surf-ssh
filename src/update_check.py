"""Update availability check — compares local version against GitHub."""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path

GITHUB_RAW_URL = (
    "https://raw.githubusercontent.com/andreisminsk/surf-ssh/main/surf-ssh-ver.txt"
)
DEFAULT_TIMEOUT = 3.0  # seconds — keep startup snappy even on poor networks


def _local_version() -> str | None:
    """Read surf-ssh-ver.txt (repo root) with fallbacks."""
    candidates = [
        Path(__file__).resolve().parent.parent / "surf-ssh-ver.txt",  # repo root
        Path.cwd() / "surf-ssh-ver.txt",
    ]
    for path in candidates:
        try:
            version = path.read_text(encoding="utf-8").strip()
            if version:
                return version
        except OSError:
            continue
    try:
        from importlib.metadata import version

        return version("surf-ssh")
    except Exception:
        return None


def _parse(version: str) -> tuple[int, ...]:
    """Parse '0.1.0' → (0, 1, 0); non-numeric parts are ignored."""
    parts = re.findall(r"\d+", version)
    return tuple(int(p) for p in parts) if parts else (0,)


def _remote_version(timeout: float = DEFAULT_TIMEOUT) -> str | None:
    try:
        with urllib.request.urlopen(GITHUB_RAW_URL, timeout=timeout) as resp:
            text = resp.read(256).decode("utf-8", "replace").strip()
        return text or None
    except Exception:
        return None


def check_update(timeout: float = DEFAULT_TIMEOUT) -> tuple[str, str] | None:
    """Return (local, remote) if a newer version exists on GitHub, else None.

    Never raises — network failures are silently ignored.
    """
    local = _local_version()
    remote = _remote_version(timeout)
    if local is None or remote is None:
        return None
    if _parse(remote) > _parse(local):
        return (local, remote)
    return None
