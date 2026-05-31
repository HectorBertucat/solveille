"""Glyphs PBF self-hostés pour le fond de carte vectoriel (B-vec).

Le style basemap Protomaps (`front/basemap-layers.js`) référence trois `text-font` :
**Noto Sans Regular / Medium / Italic**. MapLibre charge les glyphs par plages de 256 points de
code (`/glyphs/{fontstack}/{range}.pbf`). ⚠️ **Un seul 404 de glyph fait écran NOIR sur toute la
carte** → on télécharge les **256 plages complètes** des 3 fontes (couverture totale, 0 risque de
404), depuis le dépôt officiel `protomaps/basemaps-assets` (licence **SIL OFL**), **épinglé** à un
commit pour la reproductibilité et la politesse réseau (une seule requête tarball, en cache).

Sortie : `front/glyphs/<fontstack>/<range>.pbf` (servi par le mount StaticFiles `/` de l'API).
`front/glyphs/` est **gitignoré** (artefact binaire ~11 Mo, régénéré par `make glyphs` comme
`communes-index.json`/les PMTiles). Idempotent : ne retélécharge pas si les 3 fontes sont présentes.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import httpx

from solveille.common.logging import get_logger

log = get_logger("solveille.transform.build_glyphs")

#: Dépôt d'assets Protomaps épinglé (glyphs Noto Sans + licence OFL). Bump = MAJ volontaire.
_ASSETS_REPO = "protomaps/basemaps-assets"
_ASSETS_SHA = "028c18f713baecad011301ff7a69acc39bcc2ae7"
_TARBALL_URL = f"https://github.com/{_ASSETS_REPO}/archive/{_ASSETS_SHA}.tar.gz"

#: Fontstacks requis par `basemap-layers.js` (cf. tools/gen_basemap.mjs). Doivent matcher EXACTEMENT
#: les valeurs `text-font` du style (espaces compris) sinon MapLibre 404 → carte noire.
_FONTSTACKS = ("Noto Sans Regular", "Noto Sans Medium", "Noto Sans Italic")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GLYPHS_DIR = _REPO_ROOT / "front" / "glyphs"


def _already_present(glyphs_dir: Path) -> bool:
    """Vrai si les 3 fontes ont au moins leur plage latine de base (heuristique d'idempotence)."""
    return all((glyphs_dir / fs / "0-255.pbf").exists() for fs in _FONTSTACKS)


def build_glyphs(*, force: bool = False, glyphs_dir: Path = _GLYPHS_DIR) -> Path:
    """Télécharge + extrait les glyphs PBF des 3 fontes Noto Sans sous `front/glyphs/`.

    Retourne le dossier `front/glyphs/`. No-op si déjà présent (sauf `force=True`).
    """
    if not force and _already_present(glyphs_dir):
        log.info("glyphs.skip", reason="déjà présents", dir=str(glyphs_dir))
        return glyphs_dir

    log.info("glyphs.download", url=_TARBALL_URL)
    with httpx.Client(follow_redirects=True, timeout=120.0) as client:
        resp = client.get(_TARBALL_URL)
        resp.raise_for_status()
    payload = resp.content

    glyphs_dir.mkdir(parents=True, exist_ok=True)
    wanted_prefixes = {f"fonts/{fs}/": fs for fs in _FONTSTACKS}
    written = {fs: 0 for fs in _FONTSTACKS}

    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
        for member in tar:
            if not member.isfile() or not member.name.endswith(".pbf"):
                continue
            # Chemin dans le tarball : `basemaps-assets-<sha>/fonts/<fontstack>/<range>.pbf`
            rel = member.name.split("/", 1)[1] if "/" in member.name else member.name
            for prefix, fontstack in wanted_prefixes.items():
                if not rel.startswith(prefix):
                    continue
                range_name = Path(rel).name  # ex. "0-255.pbf"
                dest = glyphs_dir / fontstack / range_name
                dest.parent.mkdir(parents=True, exist_ok=True)
                extracted = tar.extractfile(member)
                if extracted is not None:
                    dest.write_bytes(extracted.read())
                    written[fontstack] += 1
                break

    missing = [fs for fs, n in written.items() if n == 0]
    if missing:
        raise RuntimeError(
            f"Glyphs manquants pour {missing} dans {_ASSETS_REPO}@{_ASSETS_SHA[:8]} — "
            "fontstack renommé ? Vérifier tools/gen_basemap.mjs et le SHA épinglé."
        )
    log.info("glyphs.done", dir=str(glyphs_dir), counts=written)
    return glyphs_dir


def main() -> None:
    import sys

    build_glyphs(force="--force" in sys.argv)


if __name__ == "__main__":
    main()
