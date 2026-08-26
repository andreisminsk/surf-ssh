"""SFTP operations wrapper with streaming support."""

from __future__ import annotations

from typing import AsyncGenerator

import asyncssh

from src.ssh.connection_pool import ConnectionPool


class SFTPClient:
    """Wraps SFTP operations with connection pool integration."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    async def open_read(
        self, host: str, path: str, chunk_size: int = 65536
    ) -> AsyncGenerator[bytes, None]:
        """Stream file content from remote host in chunks."""
        conn = await self._pool.get_connection(host)
        sem = self._pool.get_sftp_semaphore(host)
        async with sem:
            sftp = await conn.start_sftp_client()
            try:
                remote_file = await sftp.open(path, "rb")
                try:
                    while True:
                        chunk = await remote_file.read(chunk_size)
                        if not chunk:
                            break
                        yield chunk
                finally:
                    await remote_file.close()
            finally:
                sftp.exit()

    async def stat(self, host: str, path: str) -> asyncssh.SFTPAttrs:
        """Get file metadata."""
        conn = await self._pool.get_connection(host)
        sem = self._pool.get_sftp_semaphore(host)
        async with sem:
            sftp = await conn.start_sftp_client()
            try:
                return await sftp.stat(path)
            finally:
                sftp.exit()

    async def list_dir(self, host: str, path: str) -> list[asyncssh.SFTPName]:
        """List directory entries."""
        conn = await self._pool.get_connection(host)
        sem = self._pool.get_sftp_semaphore(host)
        async with sem:
            sftp = await conn.start_sftp_client()
            try:
                return await sftp.readdir(path)
            finally:
                sftp.exit()

    async def read_file(self, host: str, path: str, chunk_size: int = 65536) -> bytes:
        """Read entire file into memory. Used for images and small files."""
        conn = await self._pool.get_connection(host)
        sem = self._pool.get_sftp_semaphore(host)
        async with sem:
            sftp = await conn.start_sftp_client()
            try:
                remote_file = await sftp.open(path, "rb")
                try:
                    chunks: list[bytes] = []
                    while True:
                        chunk = await remote_file.read(chunk_size)
                        if not chunk:
                            break
                        chunks.append(chunk)
                    return b"".join(chunks)
                finally:
                    await remote_file.close()
            finally:
                sftp.exit()

    async def realpath(self, host: str, path: str) -> str:
        """Resolve symlinks to absolute path on the remote server."""
        conn = await self._pool.get_connection(host)
        sem = self._pool.get_sftp_semaphore(host)
        async with sem:
            sftp = await conn.start_sftp_client()
            try:
                return await sftp.realpath(path)
            finally:
                sftp.exit()
