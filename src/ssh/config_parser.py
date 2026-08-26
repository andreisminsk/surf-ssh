"""Parser for ~/.ssh/config files, including ProxyJump resolution."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import asyncssh


class SSHConfigParser:
    """Parses ~/.ssh/config and resolves host aliases including ProxyJump chains."""

    def __init__(self, config_path: Path | None = None) -> None:
        self._config_path = config_path or (Path.home() / ".ssh" / "config")

    def list_hosts(self) -> list[str]:
        """Return all host aliases defined in the config (excluding wildcards)."""
        hosts: list[str] = []
        if not self._config_path.exists():
            return hosts
        try:
            text = self._config_path.read_text()
        except OSError:
            return hosts
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if len(parts) >= 2 and parts[0].lower() == "host":
                for h in parts[1:]:
                    if "*" not in h and "?" not in h:
                        hosts.append(h)
        return hosts

    def get_host_config(self, host: str) -> dict[str, Any]:
        """Return resolved config for a host alias by parsing the file directly."""
        result: dict[str, Any] = {
            "hostname": host,
            "user": "",
            "port": 22,
            "proxyjump": "",
            "identityfile": [],
        }
        if not self._config_path.exists():
            return result

        try:
            text = self._config_path.read_text()
        except OSError:
            return result

        # Find matching Host block(s) — last match wins (SSH config behavior)
        matched_blocks: list[dict[str, str]] = []
        current_hosts: list[str] = []
        current_block: dict[str, str] = {}

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # Strip inline comments (e.g. "User andreis  # Your Mac username")
            stripped = re.sub(r'\s#.*$', '', stripped)
            parts = stripped.split(None, 1)
            key = parts[0].lower()
            value = parts[1].strip() if len(parts) > 1 else ""

            if key == "host":
                # Save previous block
                if current_hosts:
                    matched_blocks.append((current_hosts, current_block))
                current_hosts = value.split()
                current_block = {}
            else:
                current_block[key] = value

        if current_hosts:
            matched_blocks.append((current_hosts, current_block))

        # Apply matching blocks in order — first match wins per parameter
        # (SSH config semantics: first obtained value for each keyword is used)
        for block_hosts, block in matched_blocks:
            if not any(self._host_matches(host, h) for h in block_hosts):
                continue
            if "hostname" in block and result["hostname"] == host:
                result["hostname"] = block["hostname"]
            if "user" in block and not result["user"]:
                result["user"] = block["user"]
            if "port" in block and result["port"] == 22:
                result["port"] = int(block["port"])
            if "proxyjump" in block and not result["proxyjump"]:
                result["proxyjump"] = block["proxyjump"]
            if "identityfile" in block and not result["identityfile"]:
                # Expand ~ to home directory
                idfile = block["identityfile"]
                if idfile.startswith("~"):
                    idfile = str(Path.home() / idfile[2:])
                result["identityfile"] = [idfile]

        return result

    @staticmethod
    def _host_matches(host: str, pattern: str) -> bool:
        """Check if a host matches a pattern (supports * and ? wildcards)."""
        if "*" not in pattern and "?" not in pattern:
            return host == pattern
        # Convert glob pattern to match
        import fnmatch
        return fnmatch.fnmatch(host, pattern)

    def _sanitize_config(self) -> Path:
        """Create a sanitized copy of ~/.ssh/config with inline comments stripped.

        AsyncSSH's config parser is stricter than OpenSSH and does not tolerate
        inline comments (e.g. ``User andre # Your Mac username``). We strip them
        so the user's original config works unchanged.
        """
        sanitized_path = Path.home() / ".surf-ssh" / "ssh_config_sanitized"
        sanitized_path.parent.mkdir(parents=True, exist_ok=True)

        if not self._config_path.exists():
            return sanitized_path

        text = self._config_path.read_text()
        cleaned_lines: list[str] = []
        for line in text.splitlines():
            # Strip inline comments: '#' at start of line or preceded by whitespace
            cleaned = re.sub(r'(^|\s)#.*$', r'\1', line)
            cleaned_lines.append(cleaned.rstrip())

        sanitized_path.write_text('\n'.join(cleaned_lines))
        return sanitized_path

    def get_connect_options(self, host: str) -> asyncssh.SSHClientConnectionOptions:
        """Return AsyncSSH connection options with explicit settings (no config file parsing).

        We bypass AsyncSSH's config parser because it doesn't tolerate inline
        comments and doesn't propagate the config path to ProxyJump tunnel
        connections. Instead, we resolve all settings ourselves.
        """
        cfg = self.get_host_config(host)
        # Point to sanitized config to prevent AsyncSSH from loading the
        # original ~/.ssh/config (which has inline comments it can't parse).
        # Our explicit options below override anything in the config file.
        config_path = str(self._sanitize_config()) if self._config_path.exists() else ()
        return asyncssh.SSHClientConnectionOptions(
            config=config_path,
            host=cfg["hostname"],
            port=cfg["port"],
            username=cfg["user"] or (),
            client_keys=cfg["identityfile"] or (),
            known_hosts=None,
        )
