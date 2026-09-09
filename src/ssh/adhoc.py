"""Ad-hoc host registry — in-memory hosts without ~/.ssh/config entries.

Ad-hoc hosts (user-supplied hostname + optional user/port, authenticated
by password) get daemon-generated synthetic aliases:

    adhoc:user@host        (default port 22)
    adhoc:user@host:2222   (custom port)
    adhoc:host             (no user — SSH default)

Everything downstream (tree, file, terminal, reaper, client registry)
keys off the alias string and needs no changes. The registry dies with
the daemon — restart = clean slate (by design; passwords are memory-only).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ALIAS_PREFIX = "adhoc:"

# Hostname: letters, digits, dots, hyphens (no slashes, no whitespace)
_HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.-]*$")


class AdHocHostError(ValueError):
    """Invalid ad-hoc host specification."""


@dataclass(frozen=True)
class AdHocHost:
    hostname: str
    user: str | None
    port: int


class AdHocHostRegistry:
    """In-memory registry of ad-hoc hosts, keyed by synthetic alias."""

    def __init__(self) -> None:
        self._hosts: dict[str, AdHocHost] = {}

    @staticmethod
    def make_alias(user: str | None, hostname: str, port: int) -> str:
        """Canonical synthetic alias for an ad-hoc host."""
        if not hostname or not _HOSTNAME_RE.match(hostname):
            raise AdHocHostError(f"Invalid hostname: {hostname!r}")
        if not (1 <= port <= 65535):
            raise AdHocHostError(f"Invalid port: {port}")
        if user is not None and (not user or any(c.isspace() for c in user)):
            raise AdHocHostError(f"Invalid user: {user!r}")
        user_part = f"{user}@" if user else ""
        port_part = f":{port}" if port != 22 else ""
        return f"{ALIAS_PREFIX}{user_part}{hostname}{port_part}"

    def register(self, hostname: str, user: str | None = None, port: int = 22) -> str:
        """Register an ad-hoc host; returns its alias. Idempotent per spec."""
        alias = self.make_alias(user, hostname, port)
        self._hosts[alias] = AdHocHost(hostname=hostname, user=user, port=port)
        return alias

    def resolve(self, alias: str) -> AdHocHost | None:
        """Look up an ad-hoc host by alias. None for config aliases."""
        if not alias.startswith(ALIAS_PREFIX):
            return None
        return self._hosts.get(alias)

    def list_hosts(self) -> list[str]:
        """All registered ad-hoc aliases (for the host list merge)."""
        return list(self._hosts.keys())

    def clear(self) -> None:
        """Drop all ad-hoc hosts (daemon shutdown)."""
        self._hosts.clear()
