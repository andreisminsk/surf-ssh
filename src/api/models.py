"""Pydantic request/response models for the API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class TreeNode(BaseModel):
    path: str
    name: str
    type: Literal["file", "directory"]
    size: int | None = None
    modified: datetime | None = None


class TreeResponse(BaseModel):
    path: str
    name: str
    type: Literal["directory"] = "directory"
    truncated: bool = False
    children: list[TreeNode] = []


class FileStat(BaseModel):
    path: str
    name: str
    type: Literal["file", "directory"]
    size: int
    modified: datetime | None = None
    mode: str | None = None


class HostInfo(BaseModel):
    host: str
    status: str
    platform: str = "unix"


class HostsResponse(BaseModel):
    hosts: list[HostInfo]


class TerminalMessage(BaseModel):
    """WebSocket message for terminal sessions."""
    type: Literal["input", "resize", "ping", "output", "exited", "error", "pong", "ready"]
    data: str | None = None
    cols: int | None = None
    rows: int | None = None
    exit_code: int | None = None
    message: str | None = None
