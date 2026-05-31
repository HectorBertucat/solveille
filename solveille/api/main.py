"""Application FastAPI Solveille — lecture du mart, pages `noindex` (contrainte DVF)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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


class NoindexCacheMiddleware:
    """Garde-fou DVF (X-Robots-Tag noindex) partout + ETag/304/Cache-Control sur les routes data.

    Middleware **ASGI pur** (et surtout PAS `BaseHTTPMiddleware`) : on injecte les en-têtes en
    emballant `send`, sans jamais bufferiser le corps. `BaseHTTPMiddleware` relit le corps via un
    flux mémoire et **casse les requêtes Range des gros `FileResponse`** — le PMTiles du fond
    vectoriel (~1,5 Go) repartait en `200` (fichier entier) au lieu de `206`, de façon *racy*.
    L'ASGI pur préserve le Range/sendfile natif de StaticFiles (vérifié 32 Mo→1,5 Go).

    `/communes*` et `/meta` sont immuables pour un état donné du mart → **ETag** (mtime des marts) +
    `Cache-Control` ⇒ revalidation `304` côté CDN/navigateur (le mart change ⇒ ETag change).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        cacheable = scope["method"] == "GET" and (path.startswith("/communes") or path == "/meta")
        etag = _mart_etag() if cacheable else None
        if etag and Headers(scope=scope).get("if-none-match") == etag:
            not_modified = Response(status_code=304)
            not_modified.headers["ETag"] = etag
            not_modified.headers["Cache-Control"] = _DATA_CACHE_CONTROL
            not_modified.headers["X-Robots-Tag"] = "noindex, nofollow"
            await not_modified(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=message["headers"])
                headers["X-Robots-Tag"] = "noindex, nofollow"
                if etag:
                    headers["ETag"] = etag
                    headers["Cache-Control"] = _DATA_CACHE_CONTROL
            await send(message)

        await self.app(scope, receive, send_with_headers)


app = FastAPI(
    title="Solveille API",
    version="0.1.0",
    description=f"Nowcast communal de la pression sécheresse–argiles (RGA). {DISCLAIMER}",
)

# Front statique servi ailleurs (file:// ou autre port) → lecture seule publique. L'ordre importe :
# CORS d'abord (ajouté en 1ᵉʳ = le plus interne), noindex/cache ensuite ; les deux sont ASGI purs
# (CORSMiddleware l'est aussi) → le Range natif des PMTiles est préservé de bout en bout.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)
app.add_middleware(NoindexCacheMiddleware)


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
