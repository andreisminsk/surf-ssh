"""Host key trust (TOFU) for ad-hoc hosts.

Ad-hoc hosts aren't in ~/.ssh/known_hosts. We keep our own trust file
(~/.surf-ssh/known_hosts, OpenSSH format, 0600) — never write to the
user's ~/.ssh (surprising side effect), never skip validation (MITM hole).

Flow: connect with our trusted keys → HostKeyNotVerifiable → surface
fingerprint to the UI → user confirms → append to file → retry.
"""

from __future__ import annotations

import os
from pathlib import Path

import asyncssh


class HostKeyError(Exception):
    """Host key validation failed — typed for the API failure taxonomy."""

    def __init__(self, kind: str, fingerprint: str) -> None:
        self.kind = kind  # "host_key_unknown" | "host_key_changed"
        self.fingerprint = fingerprint
        super().__init__(f"{kind}: {fingerprint}")


class HostKeyTrust:
    """Manages ~/.surf-ssh/known_hosts for ad-hoc host TOFU."""

    def __init__(self, config_dir: Path) -> None:
        self._known_hosts = config_dir / "known_hosts"
        self._config_dir = config_dir

    def trusted_keys(self):
        """Load trusted host keys for asyncssh.connect(known_hosts=...).

        Returns an SSHKnownHosts object, or None when nothing is trusted
        yet — in which case the caller passes known_hosts=None and
        AsyncSSH falls back to the user's ~/.ssh/known_hosts (an ad-hoc
        host may already be trusted there; only truly unknown hosts
        enter the TOFU flow).
        """
        try:
            data = self._known_hosts.read_bytes()
        except OSError:
            return None
        try:
            return asyncssh.import_known_hosts(data.decode("utf-8", "replace"))
        except (OSError, ValueError):
            return None

    def trust(self, hostname: str, port: int, key_data: bytes) -> None:
        """Append a host key to the trust file (OpenSSH format, 0600)."""
        self._config_dir.mkdir(parents=True, exist_ok=True)
        marker = f"[{hostname}]:{port}" if port != 22 else hostname
        line = f"{marker} {key_data.decode('utf-8', 'replace').strip()}\n"
        with self._known_hosts.open("a", encoding="utf-8") as f:
            f.write(line)
        os.chmod(self._known_hosts, 0o600)

    def translate_connect_error(self, exc: Exception) -> Exception:
        """Map an asyncssh connect exception to HostKeyError or the original.

        AsyncSSH raises HostKeyNotVerifiable(reason) — the reason text
        distinguishes "no matching key found" (unknown) from a key
        mismatch (changed).
        """
        if not isinstance(exc, asyncssh.HostKeyNotVerifiable):
            return exc
        reason = str(exc).lower()
        # Fingerprint: AsyncSSH includes it in the reason when available
        fingerprint = ""
        if "sha256:" in reason:
            fingerprint = reason.split("sha256:")[1].split()[0].rstrip(".,)")
            fingerprint = f"SHA256:{fingerprint}"
        if "no matching" in reason or "not found" in reason or "unknown" in reason:
            return HostKeyError("host_key_unknown", fingerprint)
        return HostKeyError("host_key_changed", fingerprint)
