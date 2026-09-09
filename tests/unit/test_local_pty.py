"""Regression tests for LocalPtyManager shutdown.

Before the fix, close() closed the PTY master fd before killing the
child. On macOS, close() on a PTY master blocks uninterruptibly (U
state) while any process holds the slave side — the event loop hung
forever and the daemon never exited (auto-exit and Ctrl+C both stuck).
"""

import asyncio
import os
import platform

import pytest

from src.daemon.local_pty import LocalPtyManager

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows", reason="Unix PTY behavior"
)


async def test_close_terminates_child_and_returns():
    mgr = LocalPtyManager(["sleep", "60"], cwd="/tmp")
    await mgr.start()
    pid = mgr._pty["pid"]

    os.kill(pid, 0)  # child is alive — raises if not

    # Must complete quickly — before the fix this hung forever in close()
    await asyncio.wait_for(mgr.close(), timeout=5.0)

    # Child is gone (killed and reaped, not a zombie)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_close_is_idempotent():
    mgr = LocalPtyManager(["sleep", "60"], cwd="/tmp")
    await mgr.start()
    await mgr.close()
    await mgr.close()  # second call must not raise


async def test_close_after_natural_exit():
    mgr = LocalPtyManager(["true"], cwd="/tmp")
    await mgr.start()
    await asyncio.sleep(0.3)  # let it exit on its own
    await asyncio.wait_for(mgr.close(), timeout=5.0)
