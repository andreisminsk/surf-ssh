"""WebSocket authentication.

SessionAuthMiddleware extends BaseHTTPMiddleware, which only processes
HTTP scopes — WebSocket handshakes bypass it entirely. Every WS endpoint
must therefore call ``require_ws_auth()`` before ``websocket.accept()``.
"""

from __future__ import annotations

import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)

SESSION_COOKIE = "surf_ssh_session"


async def require_ws_auth(websocket: WebSocket) -> str | None:
    """Validate the session on a WebSocket connection.

    Checks the session cookie (sent by browsers on same-origin WS
    handshakes), falling back to a ``token`` query parameter for
    non-browser clients.

    Returns the session token on success. On failure, rejects the
    handshake (uvicorn answers with HTTP 403) and returns None.
    """
    session_mgr = getattr(websocket.app.state, "session_manager", None)
    if session_mgr is None:
        logger.error("WS auth: no session manager on app state — rejecting %s", websocket.url.path)
        await websocket.close(code=4401)
        return None

    token = websocket.cookies.get(SESSION_COOKIE) or websocket.query_params.get("token")
    if not token or not session_mgr.validate_session(token):
        logger.warning("WS auth failed for %s", websocket.url.path)
        await websocket.close(code=4401)
        return None
    return token
