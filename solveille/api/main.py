"""Application FastAPI Solveille — lecture du mart, pages `noindex` (contrainte DVF)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request
from starlette.responses import Response

from solveille.api import deps
from solveille.api.deps import MartUnavailableError
from solveille.api.routes import DISCLAIMER, router

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FRONT_DIR = _REPO_ROOT / "front"
_TILES_DIR = _REPO_ROOT / "tiles" / "out"

#: Cache des routes data : court max-age + longue fenêtre stale-while-revalidate (CDN-friendly).
_DATA_CACHE_CONTROL = "public, max-age=300, stale-while-revalidate=86400"


def _mart_etag() -> str | None:
    """ETag faible dérivé de la mtime des marts (statique + mensuel) ; None si mart absent.
    Le mart change (rebuild/refresh) ⇒ mtime change ⇒ ETag change ⇒ caches invalidés."""
    try:
        mt = 0.0
        for p in (deps.mart_path(), deps.mensuel_path()):
            if p.exists():
                mt = max(mt, p.stat().st_mtime)
        return f'W/"{int(mt)}"' if mt else None
    except OSError:
        return None


app = FastAPI(
    title="Solveille API",
    version="0.1.0",
    description=f"Nowcast communal de la pression sécheresse–argiles (RGA). {DISCLAIMER}",
)

# Front statique servi ailleurs (file:// ou autre port) → lecture seule publique.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_noindex(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Garde-fou DVF (X-Robots-Tag noindex) + cache des routes data (B2).

    Les réponses `/communes*` et `/meta` sont immuables pour un état donné du mart → on pose un
    **ETag** (mtime des marts) + `Cache-Control` (public, stale-while-revalidate) → Cloudflare/le
    navigateur revalident en `304` plutôt que de recalculer (le mart change ⇒ ETag change).
    """
    path = request.url.path
    cacheable = request.method == "GET" and (path.startswith("/communes") or path == "/meta")
    etag = _mart_etag() if cacheable else None
    if etag and request.headers.get("if-none-match") == etag:
        not_modified = Response(status_code=304)
        not_modified.headers["ETag"] = etag
        not_modified.headers["Cache-Control"] = _DATA_CACHE_CONTROL
        not_modified.headers["X-Robots-Tag"] = "noindex, nofollow"
        return not_modified
    response = await call_next(request)
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    if etag:
        response.headers["ETag"] = etag
        response.headers["Cache-Control"] = _DATA_CACHE_CONTROL
    return response


@app.exception_handler(MartUnavailableError)
async def _mart_unavailable(request: Request, exc: MartUnavailableError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
def robots() -> str:
    return "User-agent: *\nDisallow: /\n"


@app.get("/healthz", tags=["meta"])
def healthz() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(router)

# Service statique : PMTiles (range requests) + front MapLibre. Montés APRÈS les routes API
# (le mount "/" est le moins prioritaire) → `make api` sert tout sur le même origin.
if _TILES_DIR.is_dir():
    app.mount("/tiles", StaticFiles(directory=_TILES_DIR), name="tiles")
if _FRONT_DIR.is_dir():
    app.mount("/", StaticFiles(directory=_FRONT_DIR, html=True), name="front")
