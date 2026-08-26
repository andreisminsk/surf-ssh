"""WebSocket liveness endpoint — server-initiated ping/pong heartbeat."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from src.ssh.connection_pool import ConnectionPool

logger = logging.getLogger(__name__)
router = APIRouter()

PING_INTERVAL = 5.0   # seconds between pings
PING_TIMEOUT = 15.0   # seconds before marking client dead (3 missed)


def get_pool() -> ConnectionPool:
    raise NotImplementedError("Pool not configured")


@router.websocket("/liveness")
async def liveness_ws(
    websocket: WebSocket,
    host: str = "",
    pool: ConnectionPool = Depends(get_pool),
) -> None:
    """Server-initiated heartbeat endpoint.

    Client must respond to {"type":"ping"} with {"type":"pong"} within
    PING_TIMEOUT seconds. If 3 consecutive pings are missed, the server
    closes the connection and the reaper cleans up the SSH connection.
    """
    await websocket.accept()

    if not host:
        await websocket.send_json({"type": "error", "message": "Missing host parameter"})
        await websocket.close()
        return

    client_id = str(uuid.uuid4())
    pool.register_client(host, client_id, "liveness")

    logger.info("Liveness WS opened for host %s (client %s)", host, client_id)

    try:
        await websocket.send_json({"type": "ready"})

        # Server-initiated ping loop
        while True:
            await asyncio.sleep(PING_INTERVAL)
            pool.touch_client(host, client_id)

            # Send ping and wait for pong with timeout
            await websocket.send_json({"type": "ping"})

            try:
                raw = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=PING_TIMEOUT,
                )
                msg = json.loads(raw)
                if msg.get("type") == "pong":
                    pool.touch_client(host, client_id)
                # Ignore other message types
            except asyncio.TimeoutError:
                logger.info(
                    "Liveness timeout for host %s (client %s) — no pong in %ss",
                    host, client_id, PING_TIMEOUT,
                )
                break
            except WebSocketDisconnect:
                break

    except WebSocketDisconnect:
        logger.info("Liveness WS disconnected for host %s (client %s)", host, client_id)
    except json.JSONDecodeError as e:
        logger.error("Invalid liveness message: %s", e)
    except Exception as e:
        logger.error("Liveness error for host %s: %s", host, e)
    finally:
        pool.unregister_client(host, client_id)
        try:
            await websocket.close()
        except Exception:
            pass
