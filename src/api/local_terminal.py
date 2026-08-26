"""WebSocket endpoint for local terminal sessions (Local Console)."""

from __future__ import annotations

import asyncio
import json
import logging
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.daemon.local_pty import LocalPtyManager, LocalPtyRegistry, discover_shells, get_shell_by_id

logger = logging.getLogger(__name__)
router = APIRouter()

# Singleton registry for tracking active sessions and shutdown cleanup
_pty_registry = LocalPtyRegistry(max_sessions=3)


@router.get("/local/shells")
async def list_shells() -> dict:
    """List available local shells for the frontend dropdown."""
    shells = discover_shells()
    return {
        "shells": [
            {"id": s.id, "name": s.name}
            for s in shells
        ],
        "default": shells[0].id if shells else "default",
    }


@router.websocket("/local/terminal")
async def local_terminal_ws(
    websocket: WebSocket,
    shell: str = "default",
) -> None:
    """WebSocket endpoint for interactive local terminal sessions."""
    await websocket.accept()

    # Validate shell parameter
    shell_info = get_shell_by_id(shell)
    if not shell_info:
        await websocket.send_json({"type": "error", "message": f"Unknown shell: {shell}"})
        await websocket.close()
        return

    # Acquire session slot
    if not _pty_registry.acquire_slot():
        await websocket.send_json({"type": "error", "message": "Too many local terminal sessions"})
        await websocket.close()
        return

    # Start PTY in user's home directory
    home_dir = os.path.expanduser("~")
    pty_manager = LocalPtyManager(shell_info.command, cwd=home_dir)
    _pty_registry.register(pty_manager)

    try:
        await pty_manager.start()
        await websocket.send_json({"type": "ready"})

        # Forward output to WebSocket
        async def forward_output() -> None:
            while True:
                output = await pty_manager.read()
                if not output:
                    exit_code = pty_manager.get_exit_code()
                    await websocket.send_json({
                        "type": "exited",
                        "exit_code": exit_code if exit_code is not None else -1,
                    })
                    break
                await websocket.send_json({"type": "output", "data": output})

        output_task = asyncio.create_task(forward_output())

        # Receive input from WebSocket
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "input":
                await pty_manager.write(msg.get("data", ""))
            elif msg_type == "resize":
                cols = msg.get("cols", 80)
                rows = msg.get("rows", 24)
                await pty_manager.resize(cols, rows)
            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        logger.info("Local terminal WebSocket disconnected")
    except json.JSONDecodeError as e:
        logger.error("Invalid WebSocket message: %s", e)
    except Exception as e:
        logger.error("Local terminal error: %s", e)
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        if "output_task" in locals():
            output_task.cancel()
            try:
                await output_task
            except asyncio.CancelledError:
                pass
        await pty_manager.close()
        _pty_registry.unregister(pty_manager)
        try:
            await websocket.close()
        except Exception:
            pass


async def close_all_local_ptys() -> None:
    """Close all active local PTY sessions — called on shutdown."""
    await _pty_registry.close_all()
