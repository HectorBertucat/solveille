"""Connecteur INSEE — base chiffres-clés « Logement » (recensement) par commune.

Fournit le **parc de maisons par commune** (`P22_MAISON`), clé de répartition pour
redescendre à la commune le stock Fideli (maille EPCI). Le lien de téléchargement (zip CSV)
est résolu sur la page du jeu de données (ID figé par millésime — pas d'API « latest »).
Table attributaire (pas de géométrie), jointe par `CODGEO` = code INSEE commune.
"""

from __future__ import annotations

import re

from solveille.common import http
from solveille.common.config import get_settings
from solveille.common.logging import get_logger
from solveille.common.raw import RawDataset, write_manifest

log = get_logger("solveille.ingest.insee_logement")

SOURCE = "insee_logement"
# Page du jeu « Logement en 2022 » (base communale chiffres-clés). À mettre à jour au
# prochain millésime de recensement (pas d'API latest stable côté insee.fr).
PAGE_ID = "8581474"
PAGE_URL = f"https://www.insee.fr/fr/statistiques/{PAGE_ID}"
_LINK_RE = re.compile(r"/fr/statistiques/fichier/\d+/base-cc-logement-(\d+)_csv\.zip")


def _resolve_csv_zip() -> tuple[str, str]:
    """Résout (url, millésime) du zip CSV depuis la page INSEE."""
    html = http.get_text(PAGE_URL)
    m = _LINK_RE.search(html)
    if not m:
        raise LookupError(f"Lien CSV base-cc-logement introuvable sur {PAGE_URL}")
    return "https://www.insee.fr" + m.group(0), m.group(1)


def fetch() -> RawDataset:
    """Télécharge le zip CSV de la base chiffres-clés Logement communale."""
    s = get_settings()
    url, millesime = _resolve_csv_zip()
    root = s.source_raw_dir(SOURCE) / millesime
    dest = root / f"base-cc-logement-{millesime}_csv.zip"
    result = http.download(url, dest)
    log.info(
        "insee_logement.download",
        status=result.status,
        bytes=result.n_bytes,
        millesime=millesime,
    )
    manifest = write_manifest(
        SOURCE,
        root,
        source_url=url,
        srs=None,
        source_version=f"base-cc-logement-{millesime} (RP{millesime}, COG 2025)",
        files=[dest],
        extra={
            "millesime": millesime,
            "cog": "2025",
            "download_status": result.status,
            "licence": "Licence Ouverte (Etalab) — Source : Insee, Recensement de la population",
        },
    )
    return RawDataset(SOURCE, root, [dest], manifest)


def main() -> None:
    ds = fetch()
    log.info("insee_logement.done", files=[str(f) for f in ds.files])


if __name__ == "__main__":
    main()
