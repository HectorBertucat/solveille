"""Application FastAPI Solveille — lecture du mart, pages `noindex` (contrainte DVF)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.requests import Request
from starlette.responses import Response

from solveille.api.deps import MartUnavailableError
from solveille.api.routes import DISCLAIMER, router

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
    """Empêche l'indexation par les moteurs (garde-fou DVF : pas d'indexation)."""
    response = await call_next(request)
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
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
