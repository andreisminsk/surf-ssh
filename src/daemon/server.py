"""FastAPI app creation, startup, and route registration."""

from __future__ import annotations

import logging
import asyncio

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.api.files import router as files_router
from src.api.hosts import router as hosts_router
from src.api.liveness import router as liveness_router
from src.api.local_terminal import router as local_terminal_router, close_all_local_ptys
from src.api.terminal import router as terminal_router
from src.api.tree import router as tree_router
from src.security.session_auth import SessionManager
from src.ssh.config_parser import SSHConfigParser
from src.ssh.connection_pool import ConnectionPool
from src.ssh.sftp_client import SFTPClient

logger = logging.getLogger(__name__)


# Suppress noisy ConnectionResetError on Windows asyncio (Proactor loop only)
import sys

if sys.platform == "win32":
    _original_call_connection_lost = asyncio.proactor_events._ProactorBasePipeTransport._call_connection_lost

    def _silenced_call_connection_lost(self, exc):
        try:
            _original_call_connection_lost(self, exc)
        except (ConnectionResetError, ConnectionAbortedError):
            pass

    asyncio.proactor_events._ProactorBasePipeTransport._call_connection_lost = _silenced_call_connection_lost


class SessionAuthMiddleware(BaseHTTPMiddleware):
    """Validates session cookie on all API requests except the token exchange endpoint."""

    EXEMPT_PATHS = {"/api/v1/auth/exchange", "/api/v1/health"}

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Allow health check and token exchange
        if path in self.EXEMPT_PATHS:
            return await call_next(request)

        # Allow static UI routes
        if path.startswith("/ui") or path == "/" or path == "/favicon.ico":
            return await call_next(request)

        # All /api/v1/ routes require auth
        if path.startswith("/api/v1/"):
            session_mgr: SessionManager = request.app.state.session_manager
            token = request.cookies.get("surf_ssh_session")
            if not token or not session_mgr.validate_session(token):
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
            # Also check WebSocket query param for terminal connections
            if path.startswith("/api/v1/hosts/") and path.endswith("/terminal"):
                token = request.query_params.get("token", token)
                if not session_mgr.validate_session(token):
                    return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

            # Touch client liveness on host-scoped HTTP requests.
            # Extract host from paths like /api/v1/hosts/{host}/...
            # (but not /api/v1/hosts itself or /api/v1/hosts/{host}/terminal
            # which is a WebSocket handled separately).
            if path.startswith("/api/v1/hosts/") and not path.endswith("/terminal"):
                parts = path.split("/")
                if len(parts) >= 5:
                    host = parts[4]
                    pool: ConnectionPool = request.app.state.connection_pool
                    pool.touch_client(host, f"http:{token}")
                    # Auto-register if not already (HTTP-only browsing)
                    if pool.get_live_client_count(host) == 0:
                        pool.register_client(host, f"http:{token}", "http")

        return await call_next(request)


def create_app(session_manager: SessionManager) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Surf SSH",
        version="0.1.0",
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
    )

    # CORS — local only
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://localhost:8443"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Session auth middleware
    app.add_middleware(SessionAuthMiddleware)

    # Shared state
    config_parser = SSHConfigParser()
    pool = ConnectionPool(config_parser=config_parser)
    sftp_client = SFTPClient(pool)

    app.state.session_manager = session_manager
    app.state.connection_pool = pool
    app.state.sftp_client = sftp_client
    app.state.config_parser = config_parser

    # Dependency overrides
    from src.api.hosts import get_pool, get_config_parser, get_sftp_client as get_sftp_hosts
    from src.api.files import get_pool as get_pool_files, get_sftp_client
    from src.api.tree import get_sftp_client as get_sftp_tree
    from src.api.terminal import get_pool as get_pool_terminal
    from src.api.liveness import get_pool as get_pool_liveness

    app.dependency_overrides[get_pool] = lambda: pool
    app.dependency_overrides[get_pool_files] = lambda: pool
    app.dependency_overrides[get_pool_terminal] = lambda: pool
    app.dependency_overrides[get_pool_liveness] = lambda: pool
    app.dependency_overrides[get_config_parser] = lambda: config_parser
    app.dependency_overrides[get_sftp_client] = lambda: sftp_client
    app.dependency_overrides[get_sftp_hosts] = lambda: sftp_client
    app.dependency_overrides[get_sftp_tree] = lambda: sftp_client

    # Routers
    api_prefix = "/api/v1"
    app.include_router(hosts_router, prefix=api_prefix)
    app.include_router(files_router, prefix=api_prefix)
    app.include_router(tree_router, prefix=api_prefix)
    app.include_router(terminal_router, prefix=api_prefix)
    app.include_router(local_terminal_router, prefix=api_prefix)
    app.include_router(liveness_router, prefix=api_prefix)

    # Static UI
    from src.daemon.static import router as static_router
    app.include_router(static_router)

    # Token exchange endpoint
    from fastapi import Query
    from fastapi.responses import RedirectResponse

    @app.get("/api/v1/auth/exchange")
    async def exchange_token(
        token: str = Query(...),
        host: str | None = Query(default=None),
        path: str | None = Query(default=None),
    ):
        """Exchange URL token for HttpOnly cookie, then redirect to clean UI URL."""
        if not session_manager.validate_session(token):
            return JSONResponse(status_code=401, content={"detail": "Invalid token"})
        # Build clean redirect URL preserving host and path params
        params = []
        if host:
            params.append(f"host={host}")
        if path:
            params.append(f"path={path}")
        query = f"?{'&'.join(params)}" if params else ""
        response = RedirectResponse(url=f"/ui{query}", status_code=302)
        response.set_cookie(
            key="surf_ssh_session",
            value=token,
            httponly=True,
            secure=True,
            samesite="strict",
            path="/",
        )
        return response

    # Health check
    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok"}

    # Root redirect
    @app.get("/")
    async def root():
        return RedirectResponse(url="/ui", status_code=302)

    # Lifespan — clean shutdown of SSH connections
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup: start the reaper task for dead-client cleanup
        pool.start_reaper()
        yield
        # Shutdown: close all local PTY sessions and SSH connections
        logger.info("Closing all local PTY sessions...")
        await close_all_local_ptys()
        logger.info("Closing all SSH connections...")
        await pool.close_all()
        logger.info("All SSH connections closed.")

    app.router.lifespan_context = lifespan

    return app
