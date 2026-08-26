"""Directory tree endpoint with depth and entry limits."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import PurePosixPath

import asyncssh
from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.models import TreeNode, TreeResponse
from src.security.path_validator import PathValidationError, validate_path
from src.ssh.connection_pool import ConnectionPool
from src.ssh.sftp_client import SFTPClient

router = APIRouter()

MAX_DEPTH = 2
DEFAULT_LIMIT = 500
MAX_LIMIT = 5000


def get_sftp_client() -> SFTPClient:
    raise NotImplementedError("SFTP client not configured")


@router.get("/hosts/{host}/tree", response_model=TreeResponse)
async def get_tree(
    host: str,
    path: str = Query(..., description="Remote directory path"),
    depth: int = Query(1, ge=1, le=MAX_DEPTH, description="Tree depth (max 2)"),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT, description="Max entries per level"),
    sftp: SFTPClient = Depends(get_sftp_client),
) -> TreeResponse:
    """Get a depth-limited, entry-capped directory tree."""
    try:
        validated = validate_path(path)
    except PathValidationError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        return await _build_tree(host, validated, depth, limit, sftp)
    except asyncssh.Error as e:
        if "No such file" in str(e):
            raise HTTPException(status_code=404, detail="Directory not found") from e
        if "Permission denied" in str(e):
            raise HTTPException(status_code=403, detail="Permission denied") from e
        raise HTTPException(status_code=503, detail="Connection error") from e
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


async def _build_tree(
    host: str,
    path: str,
    depth: int,
    limit: int,
    sftp: SFTPClient,
) -> TreeResponse:
    """Recursively build tree response with caps."""
    entries = await sftp.list_dir(host, path)
    # Filter out . and .. entries — they cause infinite recursion in the UI
    entries = [e for e in entries if e.filename not in (".", "..")]
    entries.sort(key=lambda e: (not e.attrs.permissions or not (e.attrs.permissions & 0o040000), e.filename))

    truncated = len(entries) > limit
    entries = entries[:limit]

    children: list[TreeNode] = []
    for entry in entries:
        entry_path = str(PurePosixPath(path) / entry.filename)
        is_dir = bool(entry.attrs.permissions and (entry.attrs.permissions & 0o040000))
        modified = None
        if entry.attrs.mtime:
            modified = datetime.fromtimestamp(entry.attrs.mtime, tz=timezone.utc)

        if is_dir and depth > 1:
            # Recurse into subdirectory
            sub_tree = await _build_tree(host, entry_path, depth - 1, limit, sftp)
            children.append(TreeNode(
                path=entry_path,
                name=entry.filename,
                type="directory",
                modified=modified,
            ))
            # Note: children of subdirs are not expanded in this flat model;
            # the frontend fetches them lazily via separate requests.
        else:
            children.append(TreeNode(
                path=entry_path,
                name=entry.filename,
                type="directory" if is_dir else "file",
                size=entry.attrs.size if not is_dir else None,
                modified=modified,
            ))

    return TreeResponse(
        path=path,
        name=PurePosixPath(path).name or path,
        truncated=truncated,
        children=children,
    )
