"""Directory tree endpoint with depth and entry limits."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import PurePosixPath

import asyncssh
from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.models import TreeNode, TreeResponse
from src.security.path_validator import PathValidationError, resolve_and_validate
from src.ssh.connection_pool import ConnectionPool, PasswordRequiredError
from src.ssh.host_keys import HostKeyError
from src.ssh.sftp_client import SFTPClient

router = APIRouter()

MAX_DEPTH = 2
DEFAULT_LIMIT = 500
MAX_LIMIT = 5000


def _is_symlink(entry: asyncssh.SFTPName) -> bool:
    """Check if an SFTP entry is a symlink."""
    if entry.attrs.permissions is not None:
        return bool(entry.attrs.permissions & 0o120000)
    if entry.longname:
        return entry.longname[0] == "l"
    return False


def _is_dir_from_attrs(entry: asyncssh.SFTPName) -> bool:
    """Check if an SFTP entry is a directory based on readdir attrs.

    Does NOT follow symlinks — use _resolve_is_directory for that.
    """
    if entry.attrs.permissions is not None:
        return bool(entry.attrs.permissions & 0o040000)
    if entry.longname:
        return entry.longname[0] == "d"
    return False


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
        return await _build_tree(host, validated, depth, limit, sftp)
    except asyncssh.Error as e:
        if "No such file" in str(e):
            raise HTTPException(status_code=404, detail="Directory not found") from e
        if "Permission denied" in str(e):
            raise HTTPException(status_code=403, detail="Permission denied") from e
        raise HTTPException(status_code=503, detail="Connection error") from e
    except PasswordRequiredError:
        raise HTTPException(status_code=401, detail="password_required") from None
    except HostKeyError as e:
        raise HTTPException(
            status_code=419, detail=e.kind, headers={"X-Fingerprint": e.fingerprint}
        ) from e
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
    entries.sort(key=lambda e: (not _is_dir_from_attrs(e), e.filename))

    truncated = len(entries) > limit
    entries = entries[:limit]

    children: list[TreeNode] = []
    for entry in entries:
        entry_path = str(PurePosixPath(path) / entry.filename)
        is_dir = _is_dir_from_attrs(entry)
        # Follow symlinks to determine if the target is a directory
        if not is_dir and _is_symlink(entry):
            try:
                target_stat = await sftp.stat(host, entry_path)
                if target_stat.permissions is not None:
                    is_dir = bool(target_stat.permissions & 0o040000)
                elif getattr(target_stat, "longname", ""):
                    is_dir = target_stat.longname[0] == "d"
            except asyncssh.Error:
                pass  # Broken symlink — treat as file
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
