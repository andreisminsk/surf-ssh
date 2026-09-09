"""Host listing, status, and ad-hoc host auth endpoints."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from src.api.models import HostInfo, HostsResponse
from src.ssh.adhoc import AdHocHostError
from src.ssh.config_parser import SSHConfigParser
from src.ssh.connection_pool import ConnectionPool, PasswordRequiredError
from src.ssh.host_keys import HostKeyError
from src.ssh.sftp_client import SFTPClient

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Per-host auth attempt rate limit (prevents remote account lockout) ──
AUTH_MAX_ATTEMPTS = 5
AUTH_WINDOW_SECONDS = 60.0
_auth_attempts: dict[str, list[float]] = {}


def _check_auth_rate(host: str) -> int | None:
    """Returns retry_after seconds if rate-limited, else None."""
    now = time.monotonic()
    attempts = [t for t in _auth_attempts.get(host, []) if now - t < AUTH_WINDOW_SECONDS]
    _auth_attempts[host] = attempts
    if len(attempts) >= AUTH_MAX_ATTEMPTS:
        return int(AUTH_WINDOW_SECONDS - (now - attempts[0])) + 1
    return None


def _record_auth_attempt(host: str, success: bool) -> None:
    if success:
        _auth_attempts.pop(host, None)
    else:
        _auth_attempts.setdefault(host, []).append(time.monotonic())


def get_pool() -> ConnectionPool:
    """Dependency placeholder — overridden in create_app."""
    raise NotImplementedError("Pool not configured")


def get_config_parser() -> SSHConfigParser:
    return SSHConfigParser()


def get_sftp_client() -> SFTPClient:
    raise NotImplementedError("SFTP client not configured")


class AdHocHostRequest(BaseModel):
    hostname: str = Field(..., min_length=1, max_length=255)
    user: str | None = Field(None, max_length=64)
    port: int = Field(22, ge=1, le=65535)


class PasswordAuthRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=1024)


class TrustRequest(BaseModel):
    key_data: str = Field(..., min_length=1, max_length=8192)


@router.get("/hosts", response_model=HostsResponse)
async def list_hosts(
    pool: ConnectionPool = Depends(get_pool),
    parser: SSHConfigParser = Depends(get_config_parser),
) -> HostsResponse:
    """List all available and connected hosts (config + ad-hoc)."""
    all_hosts = parser.list_hosts() + pool.list_adhoc_hosts()
    host_infos = []
    for h in all_hosts:
        status = await pool.get_status(h)
        platform = "unix"
        if status == "connected":
            platform = await pool.detect_platform(h)
        host_infos.append(HostInfo(host=h, status=status, platform=platform))
    return HostsResponse(hosts=host_infos)


@router.post("/hosts/adhoc")
async def add_adhoc_host(
    req: AdHocHostRequest,
    pool: ConnectionPool = Depends(get_pool),
) -> dict[str, str]:
    """Register an ad-hoc host (no ~/.ssh/config entry). Session-protected."""
    try:
        alias = pool.register_adhoc(req.hostname, req.user, req.port)
    except AdHocHostError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    logger.info("Ad-hoc host registered: %s", alias)
    return {"alias": alias}


@router.post("/hosts/{host}/auth")
async def authenticate_host(
    host: str,
    req: PasswordAuthRequest,
    pool: ConnectionPool = Depends(get_pool),
) -> dict[str, str]:
    """Store a password for an ad-hoc host and attempt connection.

    Session-protected (never exempt from middleware — a pre-auth password
    endpoint would be a remote-host brute-force oracle). The password is
    held in daemon memory only; never logged, never persisted.
    """
    # Both ad-hoc and config aliases can need a password (key auth may
    # fail for either). The password is memory-only either way.
    retry_after = _check_auth_rate(host)
    if retry_after is not None:
        raise HTTPException(
            status_code=423,
            detail="Too many auth attempts",
            headers={"Retry-After": str(retry_after)},
        )

    pool.set_password(host, req.password)
    try:
        await pool.get_connection(host)
    except HostKeyError as e:
        _record_auth_attempt(host, success=False)
        pool.clear_password(host)
        raise HTTPException(
            status_code=419,
            detail=e.kind,
            headers={"X-Fingerprint": e.fingerprint},
        ) from e
    except PasswordRequiredError:
        # No password on file yet — the UI must render the prompt
        _record_auth_attempt(host, success=False)
        raise HTTPException(status_code=401, detail="password_required") from None
    except ConnectionError as e:
        _record_auth_attempt(host, success=False)
        pool.clear_password(host)
        msg = str(e)
        if "auth" in msg.lower() or "password" in msg.lower() or "permission denied" in msg.lower():
            raise HTTPException(status_code=401, detail="invalid_password") from e
        raise HTTPException(status_code=503, detail=f"Connection failed: {e}") from e

    _record_auth_attempt(host, success=True)
    logger.info("Password auth succeeded for %s", host)
    return {"status": "connected"}


@router.post("/hosts/{host}/trust")
async def trust_host_key(
    host: str,
    req: TrustRequest,
    pool: ConnectionPool = Depends(get_pool),
) -> dict[str, str]:
    """Trust a host's presented key (TOFU confirm). Session-protected."""
    if not pool.is_adhoc(host):
        raise HTTPException(status_code=404, detail="Not an ad-hoc host")
    try:
        pool.trust_host_key(host, req.key_data.encode("utf-8"))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    logger.info("Host key trusted for %s", host)
    return {"status": "trusted"}


@router.get("/hosts/{host}/status")
async def host_status(
    host: str,
    pool: ConnectionPool = Depends(get_pool),
) -> dict[str, str]:
    """Get connection status for a specific host."""
    try:
        status = await pool.get_status(host)
        platform = "unix"
        if status == "connected":
            platform = await pool.detect_platform(host)
        return {"host": host, "status": status, "platform": platform}
    except HostKeyError as e:
        raise HTTPException(
            status_code=419, detail=e.kind, headers={"X-Fingerprint": e.fingerprint}
        ) from e
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@router.get("/hosts/{host}/home")
async def host_home(
    host: str,
    sftp: SFTPClient = Depends(get_sftp_client),
) -> dict[str, str]:
    """Get the default home directory for a host."""
    try:
        home = await sftp.realpath(host, ".")
        return {"host": host, "home": home}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Cannot resolve home: {e}") from e
