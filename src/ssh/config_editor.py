"""Marker-delimited append/remove of SSH config entries.

Writes blocks to ~/.ssh/config with visible markers so cleanup is surgical:
find start marker, find end marker, delete lines between (inclusive).
Never modifies existing user content. Atomic writes via temp + os.replace.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_START_RE = re.compile(r"^#\s*surf-ssh:(.+):(ephemeral|persistent)\s*$")
_END_RE = re.compile(r"^#\s*end:surf-ssh:(.+)\s*$")


class ConfigEditorError(RuntimeError):
    """Config file operation failed."""


class ConfigEditor:
    """Manages marker-delimited entries in ~/.ssh/config."""

    def __init__(self, config_path: Path | None = None) -> None:
        self._config_path = config_path or (Path.home() / ".ssh" / "config")

    def append_entry(
        self,
        name: str,
        config: dict[str, str | int | list[str]],
        tag: str = "ephemeral",
    ) -> None:
        """Append a new Host block with markers. Atomic write."""
        if tag not in ("ephemeral", "persistent"):
            raise ConfigEditorError(f"Invalid tag: {tag}")

        lines = self._read_lines()
        block = self._format_block(name, config, tag)
        new_content = "".join(lines) + block

        self._atomic_write(new_content)
        logger.info("Created config entry %s (%s)", name, tag)

    def remove_entry(self, name: str) -> bool:
        """Remove a marker-delimited block by name. Returns True if found."""
        lines = self._read_lines()
        if not lines:
            return False

        new_lines: list[str] = []
        i = 0
        removed = False

        while i < len(lines):
            line = lines[i]
            m = _START_RE.match(line.strip())
            if m and m.group(1) == name:
                # Skip lines until the matching end marker
                removed = True
                i += 1
                while i < len(lines):
                    end_m = _END_RE.match(lines[i].strip())
                    if end_m and end_m.group(1) == name:
                        i += 1
                        break
                    i += 1
                continue
            new_lines.append(line)
            i += 1

        if removed:
            self._atomic_write("".join(new_lines))
            logger.info("Removed config entry %s", name)

        return removed

    def remove_all_ephemeral(self) -> int:
        """Remove all ephemeral entries (startup crash cleanup). Returns count."""
        lines = self._read_lines()
        if not lines:
            return 0

        new_lines: list[str] = []
        removed = 0
        i = 0

        while i < len(lines):
            line = lines[i]
            m = _START_RE.match(line.strip())
            if m and m.group(2) == "ephemeral":
                name = m.group(1)
                removed += 1
                i += 1
                while i < len(lines):
                    end_m = _END_RE.match(lines[i].strip())
                    if end_m and end_m.group(1) == name:
                        i += 1
                        break
                    i += 1
                continue
            new_lines.append(line)
            i += 1

        if removed:
            self._atomic_write("".join(new_lines))
            logger.info("Removed %d ephemeral config entries (startup cleanup)", removed)

        return removed

    def _format_block(self, name: str, config: dict, tag: str) -> str:
        """Format a marker-delimited Host block."""
        lines = [f"# surf-ssh:{name}:{tag}\n", f"Host {name}\n"]

        if config.get("hostname"):
            lines.append(f"    HostName {config['hostname']}\n")
        if config.get("user"):
            lines.append(f"    User {config['user']}\n")
        if config.get("port") and int(config["port"]) != 22:
            lines.append(f"    Port {config['port']}\n")
        if config.get("identityfile"):
            lines.append(f"    IdentityFile {config['identityfile']}\n")
        if config.get("proxyjump"):
            lines.append(f"    ProxyJump {config['proxyjump']}\n")
        for opt in config.get("options", []):
            lines.append(f"    {opt}\n")

        lines.append(f"# end:surf-ssh:{name}\n")
        return "".join(lines)

    def _read_lines(self) -> list[str]:
        """Read config file as list of lines (with newlines). Empty if missing."""
        try:
            return self._config_path.read_text().splitlines(keepends=True)
        except FileNotFoundError:
            return []
        except OSError as e:
            raise ConfigEditorError(f"Cannot read {self._config_path}: {e}") from e

    def _atomic_write(self, content: str) -> None:
        """Write content atomically: temp file + os.replace."""
        self._config_path.parent.mkdir(parents=True, exist_ok=True)

        # Preserve existing file mode, or use 0600 for new files
        if self._config_path.exists():
            mode = self._config_path.stat().st_mode & 0o777
        else:
            mode = 0o600

        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._config_path.parent),
            prefix=".surf-ssh-config-",
            suffix=".tmp",
        )
        try:
            os.write(fd, content.encode("utf-8"))
            os.close(fd)
            os.chmod(tmp_path, mode)
            os.replace(tmp_path, str(self._config_path))
        except OSError as e:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise ConfigEditorError(f"Cannot write {self._config_path}: {e}") from e
