"""Connecteur « communes basculant en classe d'exposition RGA au 1ᵉʳ juillet 2026 ».

Liste des communes dont la classe RGA change entre le zonage 2020 et le zonage 2026
(arrêté du 9 janv. 2026). Alimente le flag `basculement_2026` + les classes avant/après.
Source : data.gouv.fr (organisation « risques-adresse » / Géorisques-BRGM), CSV — résolu
via l'API data.gouv (pas d'URL en dur). Table attributaire (pas de géométrie) jointe par
`code_insee`.
"""

from __future__ import annotations

from solveille.common import datagouv, http
from solveille.common.config import get_settings
from solveille.common.logging import get_logger
from solveille.common.raw import RawDataset, write_manifest

log = get_logger("solveille.ingest.communes_bascule")

SOURCE = "communes_bascule"
DATASET = "communes-basculant-en-classe-dexposition-rga-au-1er-juillet-2026"


def fetch() -> RawDataset:
    """Télécharge le CSV des communes reclassées (résolution dynamique de la ressource)."""
    s = get_settings()
    ds = datagouv.dataset(DATASET)
    res = datagouv.pick_resource(ds, fmt="csv")
    url = res.get("url") or datagouv.stable_resource_url(res["id"])
    root = s.source_raw_dir(SOURCE)
    dest = root / "communes-basculantes-rga-2026.csv"
    result = http.download(url, dest)
    log.info("communes_bascule.download", status=result.status, bytes=result.n_bytes, url=url)
    manifest = write_manifest(
        SOURCE,
        root,
        source_url=url,
        srs=None,
        source_version=res.get("last_modified"),
        files=[dest],
        extra={
            "dataset": DATASET,
            "resource_id": res.get("id"),
            "download_status": result.status,
            "licence": "Licence Ouverte 2.0 (Etalab) — risques-adresse / Géorisques-BRGM",
        },
    )
    return RawDataset(SOURCE, root, [dest], manifest)


def main() -> None:
    ds = fetch()
    log.info("communes_bascule.done", files=[str(f) for f in ds.files])


if __name__ == "__main__":
    main()
