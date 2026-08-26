"""Static file serving for the React SPA."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

router = APIRouter()

# In dev mode, UI is served from ui/dist; in PyInstaller bundle, from _internal/ui
_STATIC_DIR = Path(__file__).parent.parent.parent / "ui" / "dist"


@router.get("/ui", response_class=HTMLResponse, response_model=None)
@router.get("/ui/", response_class=HTMLResponse, response_model=None)
async def serve_spa_root() -> FileResponse:
    """Serve the SPA index.html."""
    index = _STATIC_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="UI not built. Run: cd ui && npm run build")
    return FileResponse(index)


@router.get("/ui/{path:path}", response_class=HTMLResponse, response_model=None)
async def serve_spa_asset(path: str) -> FileResponse | HTMLResponse:
    """Serve SPA static assets or fall back to index.html for client-side routing."""
    file_path = _STATIC_DIR / path
    if file_path.exists() and file_path.is_file():
        return FileResponse(file_path)
    # SPA fallback
    index = _STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="UI not built")
