"""Manifest des assets statiques pour le cache CDN (B-perf).

Cloudflare ne cachait pas les gros PMTiles/binaires (servis `DYNAMIC`) → chaque visiteur
re-téléchargeait depuis l'origine (egress). On ajoute un **hash de contenu** en query string
(`…/communes.pmtiles?v=<hash8>`) : couplé à `Cache-Control: immutable` (Caddy) + une Cache Rule
Cloudflare, CF cache au edge, et un rebuild change le hash ⇒ cache-bust propre (sans renommer ni
nettoyer de fichiers). Les noms de fichiers restent fixes — seul le front ajoute `?v=`.

Sortie : `front/assets.js` (servi par le mount StaticFiles `/`, **gitignoré**, régénéré par
`make tiles`/`make assets`) :

    window.SOLVEILLE_ASSETS = {"communes.pmtiles":"<h>", "france.pmtiles":"<h>", …};

Chargé en `<script>` AVANT app.js (comme `basemap-layers.js`) et servi **no-cache** → les nouveaux
hashs se propagent immédiatement. Un fichier absent est **omis** du manifest (le front retombe alors
sur l'URL sans `?v=`).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import BinaryIO

from solveille.common.logging import get_logger

log = get_logger("solveille.transform.build_assets")

_HASH_LEN = 8  # 8 hex = 32 bits, largement assez pour distinguer des versions
_CHUNK = 1 << 20  # 1 Mo : hash en streaming (france.pmtiles ~1,5 Go ne tient pas en RAM)


def hash_stream(fp: BinaryIO) -> str:
    """Hash de contenu (sha256 tronqué à 8 hex) d'un flux binaire, lu par chunks de 1 Mo."""
    h = hashlib.sha256()
    while True:
        chunk = fp.read(_CHUNK)
        if not chunk:
            break
        h.update(chunk)
    return h.hexdigest()[:_HASH_LEN]


def render_manifest(mapping: Mapping[str, str]) -> str:
    """Rend le contenu de `front/assets.js` (global `window.SOLVEILLE_ASSETS`) — fonction pure."""
    obj = json.dumps(dict(mapping), separators=(",", ":"), ensure_ascii=False, sort_keys=True)
    return "window.SOLVEILLE_ASSETS = " + obj + ";\n"


def build_assets(
    *,
    tiles_dir: Path | None = None,
    front_dir: Path | None = None,
    out: Path | None = None,
) -> Path:
    """Calcule le hash de contenu des assets présents et écrit `front/assets.js`.

    Retourne le chemin du manifest. Les assets absents (ex. `france.pmtiles` non encore bâti) sont
    simplement omis. Idempotent : un contenu inchangé ⇒ hash inchangé.
    """
    repo_root = Path(__file__).resolve().parents[2]
    tiles_dir = tiles_dir or (repo_root / "tiles" / "out")
    front_dir = front_dir or (repo_root / "front")
    out = out or (front_dir / "assets.js")
    # Nom logique (= clé lue par le front) → fichier sur disque.
    sources: dict[str, Path] = {
        "communes.pmtiles": tiles_dir / "communes.pmtiles",
        "france.pmtiles": tiles_dir / "france.pmtiles",
        "communes-3d.bin": tiles_dir / "communes-3d.bin",
        "communes-3d.json": tiles_dir / "communes-3d.json",
        "communes-index.json": front_dir / "communes-index.json",
    }
    manifest: dict[str, str] = {}
    for name, path in sources.items():
        if path.is_file():
            with path.open("rb") as fp:
                manifest[name] = hash_stream(fp)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_manifest(manifest), encoding="utf-8")
    log.info("build_assets.done", out=str(out), assets=manifest)
    return out


def main() -> None:
    build_assets()


if __name__ == "__main__":
    main()
