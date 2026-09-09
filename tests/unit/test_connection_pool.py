"""Unit tests for ConnectionPool — focusing on resource management logic."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.ssh.connection_pool import ConnectionPool


@pytest.fixture
def pool():
    """Create a ConnectionPool with mock config parser."""
    from src.ssh.config_parser import SSHConfigParser
    parser = MagicMock(spec=SSHConfigParser)
    return ConnectionPool(config_parser=parser, max_connections=3, idle_timeout=1)


class TestTerminalSlots:
    """Tests for terminal session slot management."""

    def test_acquire_slot_success(self, pool):
        assert pool.acquire_terminal_slot("host1") is True

    def test_acquire_releases_slot(self, pool):
        pool.acquire_terminal_slot("host1")
        pool.release_terminal_slot("host1")
        assert pool.acquire_terminal_slot("host1") is True

    def test_max_terminals_per_host(self, pool):
        assert pool.acquire_terminal_slot("host1", "c1") is True
        assert pool.acquire_terminal_slot("host1", "c2") is True
        assert pool.acquire_terminal_slot("host1", "c3") is True
        assert pool.acquire_terminal_slot("host1", "c4") is False  # 4th rejected

    def test_same_client_id_is_one_slot(self, pool):
        # Idempotent per client — a duplicate acquire doesn't consume a slot
        assert pool.acquire_terminal_slot("host1", "c1") is True
        assert pool.acquire_terminal_slot("host1", "c1") is True
        assert pool.acquire_terminal_slot("host1", "c2") is True
        assert pool.acquire_terminal_slot("host1", "c3") is True
        assert pool.acquire_terminal_slot("host1", "c4") is False

    def test_different_hosts_independent(self, pool):
        pool.acquire_terminal_slot("host1")
        pool.acquire_terminal_slot("host1")
        pool.acquire_terminal_slot("host1")
        assert pool.acquire_terminal_slot("host2") is True

    def test_release_below_zero_safe(self, pool):
        pool.release_terminal_slot("host1")  # Should not go negative
        assert pool.acquire_terminal_slot("host1") is True

    def test_release_makes_slot_available(self, pool):
        for cid in ("c1", "c2", "c3"):
            pool.acquire_terminal_slot("host1", cid)
        assert pool.acquire_terminal_slot("host1", "c4") is False
        pool.release_terminal_slot("host1", "c2")
        assert pool.acquire_terminal_slot("host1", "c4") is True

    def test_reaper_releases_frozen_terminal_slot(self, pool):
        """A frozen terminal WS (never released) must not leak its slot.

        The reaper evicts stale clients and releases their terminal slots,
        allowing the SSH connection to close instead of leaking forever.
        """
        pool.acquire_terminal_slot("host1", "frozen-client")
        pool.register_client("host1", "frozen-client", "terminal")
        # Simulate staleness: last_seen older than ping timeout
        pool._client_registry._clients["host1"]["frozen-client"].last_seen = (
            time.monotonic() - 999.0
        )
        asyncio.run(pool._reap())
        # Slot released by the reaper
        assert "frozen-client" not in pool._terminal_slots.get("host1", set())
        # And a new terminal can acquire the freed slot
        assert pool.acquire_terminal_slot("host1", "new-client") is True


class TestSFTPSemaphore:
    """Tests for SFTP channel semaphore."""

    def test_returns_same_semaphore_per_host(self, pool):
        sem1 = pool.get_sftp_semaphore("host1")
        sem2 = pool.get_sftp_semaphore("host1")
        assert sem1 is sem2

    def test_returns_different_semaphore_per_host(self, pool):
        sem1 = pool.get_sftp_semaphore("host1")
        sem2 = pool.get_sftp_semaphore("host2")
        assert sem1 is not sem2

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self, pool):
        sem = pool.get_sftp_semaphore("host1")
        # Default max_sftp_channels = 5
        acquired = []

        async def acquire_and_hold():
            async with sem:
                acquired.append(1)
                await asyncio.sleep(0.1)

        # Launch 10 tasks — only 5 should acquire simultaneously
        tasks = [asyncio.create_task(acquire_and_hold()) for _ in range(10)]
        await asyncio.sleep(0.05)
        assert len(acquired) == 5
        await asyncio.gather(*tasks)
        assert len(acquired) == 10


class TestEviction:
    """Tests for connection eviction logic."""

    @pytest.mark.asyncio
    async def test_evict_idle_connection(self, pool):
        # Simulate an idle connection
        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = False
        pool._connections["old-host"] = mock_conn
        pool._last_access["old-host"] = time.monotonic() - 100  # Very old

        await pool._evict_if_needed()
        mock_conn.close.assert_called_once()
        assert "old-host" not in pool._connections

    @pytest.mark.asyncio
    async def test_evict_lru_at_capacity(self, pool):
        # Fill to capacity with non-idle connections (ages well under timeout)
        now = time.monotonic()
        for i in range(3):
            mock_conn = MagicMock()
            mock_conn.is_closed.return_value = False
            pool._connections[f"host{i}"] = mock_conn
            # Ages: host0=0.5s, host1=0.3s, host2=0.1s — all under 1s timeout
            pool._last_access[f"host{i}"] = now - (0.5 - i * 0.2)

        # Evict one to make room (at cap, LRU = host0 with oldest access)
        await pool._evict_if_needed()
        assert len(pool._connections) == 2
        assert "host0" not in pool._connections  # LRU evicted


class TestGetStatus:
    """Tests for connection status reporting."""

    @pytest.mark.asyncio
    async def test_status_disconnected_when_no_connection(self, pool):
        status = await pool.get_status("unknown-host")
        assert status == "disconnected"

    @pytest.mark.asyncio
    async def test_status_connected_when_active(self, pool):
        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = False
        pool._connections["host1"] = mock_conn
        pool._last_access["host1"] = time.monotonic()

        status = await pool.get_status("host1")
        assert status == "connected"

    @pytest.mark.asyncio
    async def test_status_disconnected_when_closed(self, pool):
        mock_conn = MagicMock()
        mock_conn.is_closed.return_value = True
        pool._connections["host1"] = mock_conn
        pool._last_access["host1"] = time.monotonic()

        status = await pool.get_status("host1")
        assert status == "disconnected"
