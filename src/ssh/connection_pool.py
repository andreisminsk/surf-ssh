"""AsyncSSH connection pool with per-host locking, idle eviction, and resource caps."""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import asyncssh

from src.ssh.adhoc import AdHocHostRegistry
from src.ssh.config_parser import SSHConfigParser
from src.ssh.host_keys import HostKeyError, HostKeyTrust


class PasswordRequiredError(ConnectionError):
    """Ad-hoc host needs a password — the UI must prompt (taxonomy code)."""

logger = logging.getLogger(__name__)


@dataclass
class ClientInfo:
    """Tracks a single connected client for a host."""
    client_id: str
    session_type: str  # "liveness", "terminal", "http"
    last_seen: float = field(default_factory=time.monotonic)


class ClientRegistry:
    """Per-host registry of live clients with reference counting.

    Used by the reaper to decide when an SSH connection has no live
    clients and can be closed immediately (instead of waiting for the
    600s idle timeout).
    """

    def __init__(self, ping_timeout: float = 15.0) -> None:
        self._clients: dict[str, dict[str, ClientInfo]] = {}  # host → {client_id → info}
        self._ping_timeout = ping_timeout

    def register(self, host: str, client_id: str, session_type: str = "liveness") -> None:
        """Register a new client for a host."""
        if host not in self._clients:
            self._clients[host] = {}
        self._clients[host][client_id] = ClientInfo(
            client_id=client_id,
            session_type=session_type,
        )
        logger.debug("Registered client %s for host %s (%s)", client_id, host, session_type)

    def unregister(self, host: str, client_id: str) -> None:
        """Remove a client from a host."""
        if host in self._clients:
            self._clients[host].pop(client_id, None)
            if not self._clients[host]:
                del self._clients[host]
            logger.debug("Unregistered client %s for host %s", client_id, host)

    def touch(self, host: str, client_id: str) -> None:
        """Update last_seen timestamp for a client."""
        if host in self._clients and client_id in self._clients[host]:
            self._clients[host][client_id].last_seen = time.monotonic()

    def get_live_count(self, host: str) -> int:
        """Return the number of live (non-stale) clients for a host."""
        now = time.monotonic()
        if host not in self._clients:
            return 0
        return sum(
            1 for info in self._clients[host].values()
            if now - info.last_seen <= self._ping_timeout
        )

    def get_stale_client_ids(self) -> list[tuple[str, str]]:
        """Return (host, client_id) pairs for all stale clients."""
        now = time.monotonic()
        stale = []
        for host, clients in self._clients.items():
            for client_id, info in clients.items():
                if now - info.last_seen > self._ping_timeout:
                    stale.append((host, client_id))
        return stale

    def get_hosts_with_zero_clients(self) -> list[str]:
        """Return hosts that have zero live clients."""
        return [host for host in self._clients if self.get_live_count(host) == 0]

    def clear_host(self, host: str) -> None:
        """Remove all clients for a host (used when connection is closed)."""
        self._clients.pop(host, None)

    def clear_all(self) -> None:
        """Clear all clients (used on shutdown)."""
        self._clients.clear()


class ConnectionPool:
    """Manages persistent SSH connections per host with concurrency safety."""

    def __init__(
        self,
        config_parser: SSHConfigParser | None = None,
        max_connections: int = 20,
        idle_timeout: int = 600,
        max_sftp_channels: int = 5,
        max_terminals_per_host: int = 3,
        config_dir: Path | None = None,
    ) -> None:
        self._config_parser = config_parser or SSHConfigParser()
        self._connections: dict[str, asyncssh.SSHClientConnection] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_access: dict[str, float] = {}
        self._sftp_semaphores: dict[str, asyncio.Semaphore] = {}
        self._terminal_slots: dict[str, set[str]] = {}  # host → set of client_ids
        self._max_connections = max_connections
        self._idle_timeout = idle_timeout
        self._max_sftp_channels = max_sftp_channels
        self._max_terminals_per_host = max_terminals_per_host
        self._platform_cache: dict[str, str] = {}
        self._global_lock = asyncio.Lock()
        self._client_registry = ClientRegistry(ping_timeout=15.0)
        self._reaper_task: asyncio.Task[None] | None = None
        self._reaper_interval = 5.0
        self._grace_period = 10.0  # seconds after last client leaves before closing SSH
        self._last_client_left: dict[str, float] = {}  # host → timestamp
        self._auto_exit = True  # auto-exit daemon when no live clients remain
        self._auto_exit_grace = 20.0  # seconds with zero clients before shutdown
        self._all_gone_since: float | None = None  # timestamp when all clients left
        self._ever_had_clients = False  # arms auto-exit only after first client
        self._shutdown_callback: Callable[[], None] | None = None
        # Ad-hoc hosts (no ~/.ssh/config entry) + their passwords.
        # Passwords are memory-only: never persisted, cleared on close_all.
        self._adhoc_registry = AdHocHostRegistry()
        self._passwords: dict[str, str] = {}
        # Per-host username overrides (config hosts with no User line —
        # the user supplies the account in the password prompt)
        self._username_overrides: dict[str, str] = {}
        # Hosts where key auth already failed — concurrent requests
        # fast-fail instead of re-attempting (fail2ban / lockout risk)
        self._auth_failed_hosts: set[str] = set()
        self._host_key_trust = HostKeyTrust(config_dir or Path.home() / ".surf-ssh")

    async def get_connection(self, host: str) -> asyncssh.SSHClientConnection:
        """Get or create an SSH connection for the given host alias."""
        async with self._global_lock:
            if host not in self._locks:
                self._locks[host] = asyncio.Lock()

        async with self._locks[host]:
            if host in self._connections and not self._connections[host].is_closed():
                self._last_access[host] = time.monotonic()
                return self._connections[host]

            # Fast-fail: key auth already failed and no password on file.
            # Page load fires several host-scoped calls concurrently (tree,
            # home, status) — each must not re-attempt a full SSH handshake.
            if host in self._auth_failed_hosts and self._passwords.get(host) is None:
                raise PasswordRequiredError(host)

            # Evict if at capacity
            await self._evict_if_needed()

            # Ad-hoc host: connect from registry entry + memory-held password
            adhoc = self._adhoc_registry.resolve(host)
            if adhoc is not None:
                logger.info("Opening SSH connection to %s (ad-hoc)", host)
                connect_kwargs: dict[str, Any] = {
                    "port": adhoc.port,
                    "password": self._passwords.get(host),
                    "known_hosts": self._host_key_trust.trusted_keys() or None,
                    "keepalive_interval": 30,
                }
                if adhoc.user is not None:
                    # username=None crashes AsyncSSH saslprep — omit entirely
                    connect_kwargs["username"] = adhoc.user
                try:
                    conn = await asyncssh.connect(adhoc.hostname, **connect_kwargs)
                except asyncssh.HostKeyNotVerifiable as e:
                    raise self._host_key_trust.translate_connect_error(e) from e
                except asyncssh.PermissionDenied as e:
                    logger.error("SSH auth to %s failed: %s", host, e)
                    if self._passwords.get(host) is None:
                        # No password on file — the UI must prompt for one.
                        # Remember so concurrent requests fast-fail.
                        self._auth_failed_hosts.add(host)
                        raise PasswordRequiredError(host) from e
                    raise ConnectionError(f"Cannot connect to {host}: {e}") from e
                except (asyncssh.Error, socket.gaierror, ConnectionError, OSError) as e:
                    logger.error("SSH connection to %s failed: %s", host, e)
                    raise ConnectionError(f"Cannot connect to {host}: {e}") from e
                self._connections[host] = conn
                self._last_access[host] = time.monotonic()
                return conn

            # Config alias: resolve config to check for ProxyJump
            cfg = self._config_parser.get_host_config(host)
            proxyjump = cfg.get("proxyjump", "")

            # If ProxyJump is configured, establish tunnel recursively
            tunnel = None
            if proxyjump:
                logger.info("Opening tunnel via %s for %s", proxyjump, host)
                tunnel = await self.get_connection(proxyjump)

            # Connect with explicit options (no config file parsing)
            logger.info("Opening SSH connection to %s", host)
            options = self._config_parser.get_connect_options(host)
            # Password auth for config hosts (key auth failed): pass the
            # stored password alongside config options — AsyncSSH tries
            # keys first, then password. None = key-only (normal case).
            password = self._passwords.get(host)
            # Username override (config hosts with no User line): the
            # user supplied the account in the password prompt. Overrides
            # the option's username — without it AsyncSSH falls back to
            # the local username and the correct password still fails.
            username_override = self._username_overrides.get(host)
            if username_override:
                options = asyncssh.SSHClientConnectionOptions(
                    options=options, username=username_override
                )
            try:
                conn = await asyncssh.connect(
                    cfg["hostname"],
                    port=cfg["port"],
                    options=options,
                    tunnel=tunnel,
                    keepalive_interval=30,
                    password=password,
                )
            except asyncssh.HostKeyNotVerifiable as e:
                # Config hosts validate against ~/.ssh/known_hosts —
                # surface unknown/changed keys via the same taxonomy
                raise self._host_key_trust.translate_connect_error(e) from e
            except asyncssh.PermissionDenied as e:
                logger.error("SSH auth to %s failed: %s", host, e)
                if self._passwords.get(host) is None:
                    # Key auth failed and no password on file — prompt the
                    # user (same as `ssh wb` would in a terminal). Remember
                    # the failure so concurrent requests fast-fail.
                    self._auth_failed_hosts.add(host)
                    raise PasswordRequiredError(host) from e
                raise ConnectionError(f"Cannot connect to {host}: {e}") from e
            except (asyncssh.Error, socket.gaierror, ConnectionError, OSError) as e:
                logger.error("SSH connection to %s failed: %s", host, e)
                raise ConnectionError(f"Cannot connect to {host}: {e}") from e
            self._connections[host] = conn
            self._last_access[host] = time.monotonic()
            return conn

    # ── Ad-hoc hosts & passwords ───────────────────────────────────

    def register_adhoc(self, hostname: str, user: str | None = None, port: int = 22) -> str:
        """Register an ad-hoc host; returns its synthetic alias."""
        return self._adhoc_registry.register(hostname, user, port)

    def is_adhoc(self, host: str) -> bool:
        """True if the alias refers to a registered ad-hoc host."""
        return self._adhoc_registry.resolve(host) is not None

    def list_adhoc_hosts(self) -> list[str]:
        """All registered ad-hoc aliases."""
        return self._adhoc_registry.list_hosts()

    def set_password(self, host: str, password: str) -> None:
        """Store a host's password in memory (never persisted)."""
        self._passwords[host] = password
        self._auth_failed_hosts.discard(host)

    def set_username_override(self, host: str, username: str) -> None:
        """Store a per-host username override (config hosts with no User line)."""
        self._username_overrides[host] = username

    def get_configured_user(self, host: str) -> str | None:
        """The User from ~/.ssh/config for this host, or None if unset."""
        try:
            cfg = self._config_parser.get_host_config(host)
            return cfg.get("user") or None
        except Exception:
            return None

    def clear_password(self, host: str) -> None:
        """Drop a host's password (e.g. after repeated auth failures)."""
        self._passwords.pop(host, None)

    def trust_host_key(self, host: str, key_data: bytes) -> None:
        """Trust a host's presented key (TOFU confirm from the UI)."""
        adhoc = self._adhoc_registry.resolve(host)
        if adhoc is None:
            raise ValueError(f"Not an ad-hoc host: {host}")
        self._host_key_trust.trust(adhoc.hostname, adhoc.port, key_data)

    def get_sftp_semaphore(self, host: str) -> asyncio.Semaphore:
        """Get the SFTP channel semaphore for a host."""
        if host not in self._sftp_semaphores:
            self._sftp_semaphores[host] = asyncio.Semaphore(self._max_sftp_channels)
        return self._sftp_semaphores[host]

    def acquire_terminal_slot(self, host: str, client_id: str = "") -> bool:
        """Try to acquire a terminal session slot. Returns True if available.

        Slots are tracked per client_id so the reaper can release slots of
        frozen/crashed terminal sessions (a plain counter would leak them).
        """
        slots = self._terminal_slots.setdefault(host, set())
        if len(slots) >= self._max_terminals_per_host:
            return False
        slots.add(client_id)
        return True

    def release_terminal_slot(self, host: str, client_id: str = "") -> None:
        """Release a terminal session slot (idempotent per client_id)."""
        self._terminal_slots.get(host, set()).discard(client_id)

    async def get_status(self, host: str) -> str:
        """Return connection status for a host: connected, disconnected."""
        if host in self._connections and not self._connections[host].is_closed():
            return "connected"
        return "disconnected"

    async def _evict_if_needed(self) -> None:
        """Evict idle or LRU connections when at capacity."""
        now = time.monotonic()
        # First pass: evict idle connections
        for h in list(self._connections.keys()):
            if now - self._last_access.get(h, 0) > self._idle_timeout:
                logger.info("Evicting idle connection to %s", h)
                try:
                    self._connections[h].close()
                except Exception:
                    pass
                del self._connections[h]
                self._last_access.pop(h, None)

        # Second pass: if still at cap, evict LRU
        while len(self._connections) >= self._max_connections:
            if not self._last_access:
                break
            lru_host = min(self._last_access, key=self._last_access.get)
            logger.info("Evicting LRU connection to %s", lru_host)
            try:
                self._connections[lru_host].close()
            except Exception:
                pass
            del self._connections[lru_host]
            del self._last_access[lru_host]

    async def detect_platform(self, host: str) -> str:
        """Detect the remote platform. Returns 'windows' or 'unix'.

        Uses SFTP realpath as primary detection — Windows SFTP servers
        return paths like /C:/Users/... which are unambiguous.
        Falls back to uname for Unix hosts.
        """
        if host in self._platform_cache:
            return self._platform_cache[host]

        platform = "unix"
        try:
            # Primary: check SFTP home path for Windows drive letter pattern
            from src.ssh.sftp_client import SFTPClient
            sftp = SFTPClient(self)
            home = await sftp.realpath(host, ".")
            import re
            if re.match(r"^/?[A-Za-z]:[/\\]", home):
                platform = "windows"
            else:
                # Confirm Unix with uname (fast, no risk of crashing)
                try:
                    conn = await self.get_connection(host)
                    result = await conn.run("uname -s", check=False)
                    output = (result.stdout or "").strip()
                    if any(k in output for k in ("MINGW", "MSYS", "CYGWIN")):
                        platform = "windows"
                except Exception:
                    pass
        except Exception:
            pass

        self._platform_cache[host] = platform
        return platform

    # ── Client registry / liveness API ──────────────────────────

    def register_client(self, host: str, client_id: str, session_type: str = "liveness") -> None:
        """Register a live client for a host."""
        self._client_registry.register(host, client_id, session_type)
        self._last_client_left.pop(host, None)

    def unregister_client(self, host: str, client_id: str) -> None:
        """Unregister a client from a host."""
        self._client_registry.unregister(host, client_id)
        if self._client_registry.get_live_count(host) == 0:
            self._last_client_left[host] = time.monotonic()

    def touch_client(self, host: str, client_id: str) -> None:
        """Update last-seen timestamp for a client (activity signal)."""
        self._client_registry.touch(host, client_id)

    def get_live_client_count(self, host: str) -> int:
        """Return the number of live clients for a host."""
        return self._client_registry.get_live_count(host)

    def set_auto_exit(self, enabled: bool, callback: Callable[[], None] | None = None) -> None:
        """Configure auto-exit behavior.

        When enabled, the reaper triggers the shutdown callback after
        ``_auto_exit_grace`` seconds with zero live clients across all hosts.
        """
        self._auto_exit = enabled
        self._shutdown_callback = callback

    def _get_total_live_clients(self) -> int:
        """Return total live clients across all hosts."""
        hosts = set(self._client_registry._clients.keys())
        return sum(self._client_registry.get_live_count(h) for h in hosts)

    # ── Reaper ───────────────────────────────────────────────────

    def start_reaper(self) -> None:
        """Start the background reaper task (call from lifespan startup)."""
        if self._reaper_task is None or self._reaper_task.done():
            self._reaper_task = asyncio.create_task(self._reaper_loop())

    async def stop_reaper(self) -> None:
        """Stop the reaper task (call from lifespan shutdown)."""
        if self._reaper_task and not self._reaper_task.done():
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                pass
            self._reaper_task = None

    async def _reaper_loop(self) -> None:
        """Periodically evict stale clients and close orphaned SSH connections."""
        while True:
            try:
                await asyncio.sleep(self._reaper_interval)
                await self._reap()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Reaper error: %s", e)

    async def _reap(self) -> None:
        """Single reaper pass: evict stale clients, close connections with no live clients."""
        now = time.monotonic()

        # 1. Evict stale clients
        for host, client_id in self._client_registry.get_stale_client_ids():
            logger.info("Evicting stale client %s for host %s", client_id, host)
            # A frozen terminal WS never releases its slot — release it here
            # so the SSH connection can be closed instead of leaking.
            self.release_terminal_slot(host, client_id)
            self._client_registry.unregister(host, client_id)
            if self._client_registry.get_live_count(host) == 0:
                self._last_client_left[host] = now

        # 2. Close SSH connections with zero live clients after grace period
        for host in self._client_registry.get_hosts_with_zero_clients():
            left_at = self._last_client_left.get(host)
            if left_at is None:
                continue
            if now - left_at < self._grace_period:
                continue
            if host in self._connections and not self._connections[host].is_closed():
                # Don't close if there are active terminal sessions still
                if len(self._terminal_slots.get(host, ())) > 0:
                    continue
                logger.info("Reaper: closing SSH connection to %s (no live clients)", host)
                try:
                    self._connections[host].close()
                except Exception:
                    pass
                del self._connections[host]
                self._last_access.pop(host, None)
            self._client_registry.clear_host(host)
            self._last_client_left.pop(host, None)

        # 3. Auto-exit: shut down daemon if no live clients across ALL hosts.
        # Only arms after at least one client has connected (so a freshly
        # started daemon with no clients doesn't immediately exit).
        if self._auto_exit and self._shutdown_callback:
            total_live = self._get_total_live_clients()
            if total_live == 0:
                if self._ever_had_clients:
                    if self._all_gone_since is None:
                        self._all_gone_since = now
                    elif now - self._all_gone_since >= self._auto_exit_grace:
                        logger.info(
                            "No live clients for %.0fs — requesting daemon shutdown",
                            self._auto_exit_grace,
                        )
                        self._shutdown_callback()
            else:
                self._ever_had_clients = True
                self._all_gone_since = None

    async def close_all(self) -> None:
        """Close all connections on shutdown."""
        await self.stop_reaper()
        self._client_registry.clear_all()
        self._last_client_left.clear()
        # Zero password copies survive shutdown (hard invariant #3)
        self._passwords.clear()
        self._adhoc_registry.clear()
        self._auth_failed_hosts.clear()
        self._username_overrides.clear()
        for host, conn in list(self._connections.items()):
            try:
                conn.close()
            except Exception:
                pass
        self._connections.clear()
        self._last_access.clear()
