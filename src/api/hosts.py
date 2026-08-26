"""Host listing and status endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from src.api.models import HostInfo, HostsResponse
from src.ssh.config_parser import SSHConfigParser
from src.ssh.connection_pool import ConnectionPool
from src.ssh.sftp_client import SFTPClient

router = APIRouter()


def get_pool() -> ConnectionPool:
    """Dependency placeholder — overridden in create_app."""
    raise NotImplementedError("Pool not configured")


def get_config_parser() -> SSHConfigParser:
    return SSHConfigParser()


def get_sftp_client() -> SFTPClient:
    raise NotImplementedError("SFTP client not configured")


@router.get("/hosts", response_model=HostsResponse)
async def list_hosts(
    pool: ConnectionPool = Depends(get_pool),
    parser: SSHConfigParser = Depends(get_config_parser),
) -> HostsResponse:
    """List all available and connected hosts."""
    all_hosts = parser.list_hosts()
    host_infos = []
    for h in all_hosts:
        status = await pool.get_status(h)
        platform = "unix"
        if status == "connected":
            platform = await pool.detect_platform(h)
        host_infos.append(HostInfo(host=h, status=status, platform=platform))
    return HostsResponse(hosts=host_infos)


@router.get("/hosts/{host}/status")
async def host_status(
    host: str,
    pool: ConnectionPool = Depends(get_pool),
) -> dict[str, str]:
    """Get connection status for a specific host."""
    status = await pool.get_status(host)
    platform = "unix"
    if status == "connected":
        platform = await pool.detect_platform(host)
    return {"host": host, "status": status, "platform": platform}


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
