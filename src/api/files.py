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
from src.security.path_validator import PathValidationError, validate_path
from src.ssh.connection_pool import ConnectionPool
from src.ssh.sftp_client import SFTPClient

router = APIRouter()


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
        validated = validate_path(path)
    except PathValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        # Determine content type
        suffix = PurePosixPath(validated).suffix.lower()
        content_type, _ = mimetypes.guess_type(validated)
        if content_type is None:
            content_type = "application/octet-stream"

        # Check if it's a directory
        stat_info = await sftp.stat(host, validated)
        from asyncssh import SFTPAttrs

        if stat_info.permissions and (stat_info.permissions & 0o040000):
            raise HTTPException(status_code=400, detail="Path is a directory, not a file")

        file_size = stat_info.size or 0

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
        validated = validate_path(path)
    except PathValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        stat_info = await sftp.stat(host, validated)
        is_dir = bool(stat_info.permissions and (stat_info.permissions & 0o040000))
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
        validated = validate_path(path)
    except PathValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
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
