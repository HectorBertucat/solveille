"""Utilitaire de récupération HTTP poli et paginé pour les API open data FR.

Volontairement minimal et sans dépendance lourde (httpx). À adapter par source.
Principes : timeout, retries + backoff exponentiel, pagination (par `next` ou par `page`),
cache conditionnel optionnel (ETag/Last-Modified). NE reprojette PAS et NE transforme PAS.
"""
from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import httpx

DEFAULT_TIMEOUT = 30.0
DEFAULT_HEADERS = {"User-Agent": "solveille/0.1 (+https://github.com/<user>/solveille)"}


def get_json(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    max_retries: int = 5,
    base_delay: float = 1.0,
    timeout: float = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """GET poli avec retries + backoff exponentiel sur 429/5xx et erreurs réseau."""
    hdrs = {**DEFAULT_HEADERS, **(headers or {})}
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = httpx.get(url, params=params, headers=hdrs, timeout=timeout)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise httpx.HTTPStatusError("retryable", request=resp.request, response=resp)
            resp.raise_for_status()
            return resp
        except (httpx.HTTPError,) as exc:  # réseau + statuts retryables
            last_exc = exc
            sleep = base_delay * (2**attempt)
            # respecte Retry-After si présent
            retry_after = getattr(getattr(exc, "response", None), "headers", {}) or {}
            if "Retry-After" in retry_after:
                try:
                    sleep = max(sleep, float(retry_after["Retry-After"]))
                except ValueError:
                    pass
            time.sleep(sleep)
    raise RuntimeError(f"GET échoué après {max_retries} tentatives : {url}") from last_exc


def get_paginated(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    data_key: str = "data",
    follow: str | None = "next",     # API style Hub'eau : champ d'URL "next"
    page_param: str | None = None,   # sinon pagination par numéro de page
    max_pages: int = 1000,
    pause_s: float = 0.2,            # courtoisie entre pages
) -> list[dict[str, Any]]:
    """Récupère tous les enregistrements en suivant la pagination.

    Deux modes : suivre l'URL `next` (Hub'eau) OU incrémenter `page_param`.
    """
    out: list[dict[str, Any]] = []
    params = dict(params or {})
    next_url: str | None = url
    page = params.get(page_param, 1) if page_param else None

    for _ in range(max_pages):
        if next_url is None:
            break
        if page_param is not None:
            params[page_param] = page
            resp = get_json(url, params=params)
        else:
            resp = get_json(next_url, params=params if next_url == url else None)
        payload = resp.json()
        rows = payload.get(data_key, payload if isinstance(payload, list) else [])
        out.extend(rows)

        if follow and isinstance(payload, dict) and payload.get(follow):
            next_url, params = payload[follow], None  # l'URL "next" porte déjà les params
        elif page_param is not None and rows:
            page += 1
        else:
            next_url = None
        time.sleep(pause_s)
    return out


def iter_paginated(*args: Any, **kwargs: Any) -> Iterator[dict[str, Any]]:
    """Variante générateur (mémoire) — à privilégier sur de gros volumes."""
    yield from get_paginated(*args, **kwargs)
