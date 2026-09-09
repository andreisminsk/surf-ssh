"""TOTP verification endpoint — the browser-direct auth path."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.security.totp import RateLimitError

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/auth/verify-totp")
async def verify_totp(request: Request) -> JSONResponse:
    """Verify a TOTP code or backup code; on success set the session cookie.

    This endpoint IS the authentication — exempt from cookie auth in
    SessionAuthMiddleware. Rate-limited globally (all traffic is
    localhost; per-IP limiting is meaningless).
    """
    session_mgr = request.app.state.session_manager
    totp_mgr = request.app.state.totp_manager

    if not totp_mgr.is_enabled():
        return JSONResponse(status_code=404, content={"detail": "2FA not enabled"})

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=422, content={"detail": "Invalid JSON body"})

    code = body.get("code")
    backup_code = body.get("backup_code")

    try:
        if backup_code:
            ok = totp_mgr.verify_backup_code(backup_code)
            kind = "backup_code"
        elif code:
            ok = totp_mgr.verify(code)
            kind = "totp"
        else:
            return JSONResponse(status_code=422, content={"detail": "code or backup_code required"})
    except RateLimitError as e:
        logger.warning("TOTP verify rate-limited (retry after %ss)", e.retry_after)
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many attempts", "retry_after": e.retry_after},
            headers={"Retry-After": str(e.retry_after)},
        )

    if not ok:
        logger.warning("TOTP verify failed (%s)", kind)
        return JSONResponse(status_code=401, content={"detail": "Invalid code"})

    # Success — mint a session and set the cookie (same as /auth/exchange)
    token = session_mgr.create_session()
    logger.info("TOTP verify success (%s) — session issued", kind)
    response = JSONResponse(status_code=200, content={"status": "ok"})
    response.set_cookie(
        key="surf_ssh_session",
        value=token,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )
    return response
