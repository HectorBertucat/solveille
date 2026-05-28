"""Client HTTP poli : timeout, backoff exponentiel (429/5xx + réseau), User-Agent
explicite, et téléchargement en streaming avec cache conditionnel (ETag/Last-Modified).

Aucune transformation ici : on télécharge le brut. La reprojection EPSG:2154 et le
nettoyage vivent dans `solveille/transform/`.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from solveille.common.config import USER_AGENT, get_settings
from solveille.common.logging import get_logger

log = get_logger("solveille.http")

#: Statuts HTTP que l'on réessaie (le reste des 4xx échoue immédiatement).
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def build_client(
    *, timeout: float | None = None, headers: dict[str, str] | None = None
) -> httpx.Client:
    """Crée un client httpx avec User-Agent Solveille et suivi des redirections."""
    s = get_settings()
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    return httpx.Client(
        timeout=timeout if timeout is not None else s.http_timeout,
        headers=hdrs,
        follow_redirects=True,
    )


def _backoff_sleep(attempt: int, base_delay: float, resp: httpx.Response | None) -> None:
    delay = base_delay * (2.0**attempt)
    if resp is not None and (ra := resp.headers.get("Retry-After")):
        with contextlib.suppress(ValueError):
            delay = max(delay, float(ra))
    time.sleep(delay)


def get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    client: httpx.Client | None = None,
    max_retries: int | None = None,
    base_delay: float | None = None,
) -> httpx.Response:
    """GET poli avec retries + backoff (429/5xx + erreurs réseau). 4xx non-retry = lève."""
    s = get_settings()
    retries = max_retries if max_retries is not None else s.http_max_retries
    delay = base_delay if base_delay is not None else s.http_pause_s
    own = client is None
    cli = client or build_client()
    last_exc: Exception | None = None
    try:
        for attempt in range(retries):
            try:
                resp = cli.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:  # réseau / timeout
                last_exc = exc
                log.warning("http.error", url=url, attempt=attempt, error=str(exc))
                _backoff_sleep(attempt, delay, None)
                continue
            if resp.status_code in RETRYABLE_STATUS:
                last_exc = httpx.HTTPStatusError("retryable", request=resp.request, response=resp)
                log.warning("http.retryable", url=url, status=resp.status_code, attempt=attempt)
                _backoff_sleep(attempt, delay, resp)
                continue
            resp.raise_for_status()  # autres 4xx → propage sans retry
            return resp
        raise RuntimeError(f"GET échoué après {retries} tentatives : {url}") from last_exc
    finally:
        if own:
            cli.close()


def get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    client: httpx.Client | None = None,
) -> Any:
    """GET poli renvoyant le corps JSON décodé."""
    return get(url, params=params, headers=headers, client=client).json()


def get_text(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    client: httpx.Client | None = None,
) -> str:
    """GET poli renvoyant le corps texte."""
    return get(url, params=params, headers=headers, client=client).text


@dataclass
class DownloadResult:
    """Résultat d'un téléchargement (idempotent via cache conditionnel)."""

    url: str
    path: Path
    status: str  # "downloaded" | "not_modified"
    sha256: str | None = None
    n_bytes: int | None = None
    etag: str | None = None
    last_modified: str | None = None


def _cache_path(dest: Path) -> Path:
    return dest.with_name(dest.name + ".httpcache.json")


def download(
    url: str,
    dest: Path | str,
    *,
    headers: dict[str, str] | None = None,
    conditional: bool = True,
    client: httpx.Client | None = None,
    max_retries: int | None = None,
    base_delay: float | None = None,
) -> DownloadResult:
    """Télécharge `url` vers `dest` en streaming (sha256, écriture atomique).

    Si `conditional` et qu'un cache local existe, envoie `If-None-Match` /
    `If-Modified-Since` et renvoie `status="not_modified"` sur 304 (idempotence,
    politesse réseau).
    """
    s = get_settings()
    retries = max_retries if max_retries is not None else s.http_max_retries
    delay = base_delay if base_delay is not None else s.http_pause_s
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cache = _cache_path(dest)

    req_headers = dict(headers or {})
    prev: dict[str, Any] = {}
    if conditional and dest.exists() and cache.exists():
        try:
            prev = json.loads(cache.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            prev = {}
        if prev.get("etag"):
            req_headers["If-None-Match"] = prev["etag"]
        if prev.get("last_modified"):
            req_headers["If-Modified-Since"] = prev["last_modified"]

    own = client is None
    cli = client or build_client()
    last_exc: Exception | None = None
    try:
        for attempt in range(retries):
            try:
                with cli.stream("GET", url, headers=req_headers) as resp:
                    if resp.status_code == 304 and dest.exists():
                        log.info("http.not_modified", url=url, path=str(dest))
                        return DownloadResult(
                            url,
                            dest,
                            "not_modified",
                            sha256=prev.get("sha256"),
                            n_bytes=prev.get("bytes"),
                            etag=prev.get("etag"),
                            last_modified=prev.get("last_modified"),
                        )
                    if resp.status_code in RETRYABLE_STATUS:
                        resp.read()
                        last_exc = httpx.HTTPStatusError(
                            "retryable", request=resp.request, response=resp
                        )
                        log.warning(
                            "http.retryable", url=url, status=resp.status_code, attempt=attempt
                        )
                        _backoff_sleep(attempt, delay, resp)
                        continue
                    resp.raise_for_status()
                    tmp = dest.with_name(dest.name + ".part")
                    digest = hashlib.sha256()
                    size = 0
                    with tmp.open("wb") as fh:
                        for chunk in resp.iter_bytes(chunk_size=1 << 20):
                            fh.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                    tmp.replace(dest)
                    sha = digest.hexdigest()
                    etag = resp.headers.get("ETag")
                    lm = resp.headers.get("Last-Modified")
                    cache.write_text(
                        json.dumps(
                            {
                                "url": url,
                                "etag": etag,
                                "last_modified": lm,
                                "sha256": sha,
                                "bytes": size,
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                    log.info("http.downloaded", url=url, path=str(dest), bytes=size)
                    return DownloadResult(
                        url,
                        dest,
                        "downloaded",
                        sha256=sha,
                        n_bytes=size,
                        etag=etag,
                        last_modified=lm,
                    )
            except httpx.HTTPError as exc:
                last_exc = exc
                log.warning("http.download_error", url=url, attempt=attempt, error=str(exc))
                _backoff_sleep(attempt, delay, getattr(exc, "response", None))
        raise RuntimeError(
            f"Téléchargement échoué après {retries} tentatives : {url}"
        ) from last_exc
    finally:
        if own:
            cli.close()
