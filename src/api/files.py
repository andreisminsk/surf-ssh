"""File content, metadata, and download endpoints."""

from __future__ import annotations

import mimetypes
from datetime import datetime, timezone
from pathlib import PurePosixPath

# Register types that Python doesn't know but are text-based
mimetypes.add_type("application/xml", ".plist")
mimetypes.add_type("text/plain", ".bat")
mimetypes.add_type("text/plain", ".cmd")
mimetypes.add_type("text/plain", ".ps1")
mimetypes.add_type("text/plain", ".psm1")

import asyncssh
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse, Response

from src.api.models import FileStat
from src.security.path_validator import PathValidationError, resolve_and_validate
from src.ssh.connection_pool import ConnectionPool, PasswordRequiredError
from src.ssh.host_keys import HostKeyError
from src.ssh.sftp_client import SFTPClient

router = APIRouter()

# Hard cap on streamed/downloaded file size (2 GiB) — protects the local
# daemon and the SFTP channel from unbounded transfers (disk images etc.)
MAX_TRANSFER_SIZE = 2 * 1024 * 1024 * 1024


def get_pool() -> ConnectionPool:
    raise NotImplementedError("Pool not configured")


def get_sftp_client() -> SFTPClient:
    raise NotImplementedError("SFTP client not configured")


@router.get("/hosts/{host}/file")
async def get_file(
    host: str,
    path: str = Query(..., description="Remote file path"),
    sftp: SFTPClient = Depends(get_sftp_client),
) -> StreamingResponse:
    """Stream file content from the remote host."""
    try:
        validated = await resolve_and_validate(sftp, host, path)
    except PathValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except PasswordRequiredError:
        raise HTTPException(status_code=401, detail="password_required") from None
    except HostKeyError as e:
        raise HTTPException(
            status_code=419, detail=e.kind, headers={"X-Fingerprint": e.fingerprint}
        ) from e
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    try:
        # Determine content type
        suffix = PurePosixPath(validated).suffix.lower()
        content_type, _ = mimetypes.guess_type(validated)
        if content_type is None:
            content_type = "application/octet-stream"

        # Check if it's a directory
        stat_info = await sftp.stat(host, validated)

        if stat_info.permissions is not None:
            is_dir = bool(stat_info.permissions & 0o040000)
        else:
            # Fallback: check if stat longname starts with 'd'
            is_dir = bool(getattr(stat_info, "longname", "") and stat_info.longname[0] == "d")
        if is_dir:
            raise HTTPException(status_code=400, detail="Path is a directory, not a file")

        file_size = stat_info.size or 0

        if file_size > MAX_TRANSFER_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"File too large to view ({file_size} bytes, limit {MAX_TRANSFER_SIZE})",
            )

        # For images and files < 50MB, read into memory for reliable delivery
        # (StreamingResponse can stall over ProxyJump tunnels)
        if file_size < 50 * 1024 * 1024:
            body = await sftp.read_file(host, validated)
            import hashlib
            etag = hashlib.md5(body).hexdigest()
            headers = {
                "Cache-Control": "private, max-age=86400, immutable" if content_type.startswith("image/") else "private, max-age=300",
                "ETag": f'"{etag}"',
            }
            return Response(
                content=body,
                media_type=content_type,
                headers=headers,
            )

        return StreamingResponse(
            sftp.open_read(host, validated),
            media_type=content_type,
            headers={"Cache-Control": "private, max-age=300"},
        )
    except PasswordRequiredError:
        raise HTTPException(status_code=401, detail="password_required") from None
    except HostKeyError as e:
        raise HTTPException(
            status_code=419, detail=e.kind, headers={"X-Fingerprint": e.fingerprint}
        ) from e
    except asyncssh.Error as e:
        if "No such file" in str(e):
            raise HTTPException(status_code=404, detail="File not found") from e
        if "Permission denied" in str(e):
            raise HTTPException(status_code=403, detail="Permission denied") from e
        raise HTTPException(status_code=503, detail="Connection error") from e


@router.get("/hosts/{host}/stat", response_model=FileStat)
async def get_stat(
    host: str,
    path: str = Query(..., description="Remote file path"),
    sftp: SFTPClient = Depends(get_sftp_client),
) -> FileStat:
    """Get file metadata from the remote host."""
    try:
        validated = await resolve_and_validate(sftp, host, path)
    except PathValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except PasswordRequiredError:
        raise HTTPException(status_code=401, detail="password_required") from None
    except HostKeyError as e:
        raise HTTPException(
            status_code=419, detail=e.kind, headers={"X-Fingerprint": e.fingerprint}
        ) from e
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    try:
        stat_info = await sftp.stat(host, validated)
        if stat_info.permissions is not None:
            is_dir = bool(stat_info.permissions & 0o040000)
        else:
            is_dir = bool(getattr(stat_info, "longname", "") and stat_info.longname[0] == "d")
        modified = None
        if stat_info.mtime:
            modified = datetime.fromtimestamp(stat_info.mtime, tz=timezone.utc)

        return FileStat(
            path=validated,
            name=PurePosixPath(validated).name,
            type="directory" if is_dir else "file",
            size=stat_info.size or 0,
            modified=modified,
            mode=oct(stat_info.permissions)[2:] if stat_info.permissions else None,
        )
    except asyncssh.Error as e:
        if "No such file" in str(e):
            raise HTTPException(status_code=404, detail="File not found") from e
        if "Permission denied" in str(e):
            raise HTTPException(status_code=403, detail="Permission denied") from e
        raise HTTPException(status_code=503, detail="Connection error") from e


@router.get("/hosts/{host}/download")
async def download_file(
    host: str,
    path: str = Query(..., description="Remote file path"),
    sftp: SFTPClient = Depends(get_sftp_client),
) -> StreamingResponse:
    """Download a binary file with Content-Disposition attachment header."""
    try:
        validated = await resolve_and_validate(sftp, host, path)
    except PathValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except PasswordRequiredError:
        raise HTTPException(status_code=401, detail="password_required") from None
    except HostKeyError as e:
        raise HTTPException(
            status_code=419, detail=e.kind, headers={"X-Fingerprint": e.fingerprint}
        ) from e
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    try:
        stat_info = await sftp.stat(host, validated)
        file_size = stat_info.size or 0
        if file_size > MAX_TRANSFER_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"File too large to download ({file_size} bytes, limit {MAX_TRANSFER_SIZE})",
            )
        filename = PurePosixPath(validated).name
        return StreamingResponse(
            sftp.open_read(host, validated),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except asyncssh.Error as e:
        if "No such file" in str(e):
            raise HTTPException(status_code=404, detail="File not found") from e
        raise HTTPException(status_code=503, detail="Connection error") from e
