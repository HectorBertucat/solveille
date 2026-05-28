"""Zone brute (`data/raw/<source>/`) : hash, manifeste `_meta.json`, traçabilité.

Le manifeste porte les champs qui remontent jusqu'à l'UI (`last_updated_*`) : source,
url, version source, date de récupération, volumétrie, empreintes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def sha256_file(path: Path | str, chunk: int = 1 << 20) -> str:
    """Empreinte SHA-256 d'un fichier (lecture par blocs, mémoire constante)."""
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def utc_now_iso() -> str:
    """Horodatage UTC ISO-8601 (suffixe Z)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class RawDataset:
    """Sortie d'un connecteur `fetch()` : où est le brut et son manifeste."""

    source: str
    root: Path
    files: list[Path]
    manifest_path: Path


def write_manifest(
    source: str,
    root: Path | str,
    *,
    source_url: str,
    srs: str | None = None,
    source_version: str | None = None,
    n_rows: int | None = None,
    files: Iterable[Path | str] | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Écrit `data/raw/<source>/_meta.json` (crée le répertoire si besoin)."""
    file_infos: list[dict[str, Any]] = []
    for f in files or []:
        p = Path(f)
        if p.exists() and p.is_file():
            file_infos.append({"name": p.name, "bytes": p.stat().st_size, "sha256": sha256_file(p)})
    manifest: dict[str, Any] = {
        "source": source,
        "source_url": source_url,
        "srs": srs,
        "source_version": source_version,
        "date_fetch": utc_now_iso(),
        "n_rows": n_rows,
        "files": file_infos,
    }
    if extra:
        manifest.update(extra)
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    mpath = root_path / "_meta.json"
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return mpath


def read_manifest(root: Path | str) -> dict[str, Any] | None:
    """Lit le manifeste d'une source brute, ou `None` s'il n'existe pas."""
    mpath = Path(root) / "_meta.json"
    if mpath.exists():
        return json.loads(mpath.read_text(encoding="utf-8"))
    return None
