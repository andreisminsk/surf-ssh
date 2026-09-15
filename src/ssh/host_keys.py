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

    def __init__(self, kind: str, fingerprint: str, key_data: str = "") -> None:
        self.kind = kind  # "host_key_unknown" | "host_key_changed"
        self.fingerprint = fingerprint
        self.key_data = key_data  # full public key (OpenSSH format) for TOFU
        super().__init__(f"{kind}: {fingerprint}")


class TofuSSHClient(asyncssh.SSHClient):
    """SSHClient that captures the server host key during validation.

    validate_host_public_key is called only when the key is NOT in the
    trusted set — exactly the TOFU case. We record the key and return
    False (connection fails), then surface it to the user for explicit
    confirmation. On retry after trust, the key is in known_hosts and
    validation succeeds normally.
    """

    def __init__(self) -> None:
        self.captured_key: asyncssh.SSHKey | None = None

    def validate_host_public_key(self, host: str, addr: str, port: int, key) -> bool:
        self.captured_key = key
        return False  # fail the connection — the pool raises HostKeyError


class HostKeyTrust:
    """Manages ~/.surf-ssh/known_hosts for ad-hoc host TOFU."""

    def __init__(self, config_dir: Path) -> None:
        self._known_hosts = config_dir / "known_hosts"
        self._config_dir = config_dir

    def trusted_keys(self):
        """Load trusted host keys for asyncssh.connect(known_hosts=...).

        Always returns an SSHKnownHosts object — empty when nothing is
        trusted yet. An empty object means every presented key is
        "not trusted", which triggers validate_host_public_key on the
        TofuSSHClient → key captured → 419 → user confirms → stored.
        Never return None: that disables validation entirely (MITM gap).
        """
        try:
            data = self._known_hosts.read_bytes()
        except OSError:
            return asyncssh.import_known_hosts("")
        try:
            return asyncssh.import_known_hosts(data.decode("utf-8", "replace"))
        except (OSError, ValueError):
            return asyncssh.import_known_hosts("")

    def merged_known_hosts(self):
        """One SSHKnownHosts combining the user's ~/.ssh/known_hosts and
        our TOFU store. Used by config-host connections so keys trusted
        via the UI dialog validate alongside the user's own entries."""
        parts: list[str] = []
        user_kh = Path.home() / ".ssh" / "known_hosts"
        try:
            if user_kh.exists():
                parts.append(user_kh.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
        try:
            if self._known_hosts.exists():
                parts.append(self._known_hosts.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
        return asyncssh.import_known_hosts("\n".join(parts))

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
