"""Connecteur RGA 2026 — zonage retrait-gonflement des argiles (arrêté du 9 janv. 2026).

Voie d'acquisition : le SHP officiel Géorisques n'est pas accessible par API (formulaire
JS, absent de la Géoplateforme et de data.gouv en vecteur). On utilise donc la **voie de
repli validée** : le FeatureServer ArcGIS hébergé (copie Esri France / MRN du zonage
officiel), bornable par département et paginable. La couche est **dissoute par
(département × niveau)** — attributs `DPT`, `NIVEAU` (1=Faible, 2=Moyen, 3=Fort), `ALEA`.

Sortie GeoJSON en **EPSG:4326** (ArcGIS) → reprojection vers 2154 dans `transform/`.
Double attribution : Géorisques/BRGM/MTE (source, Licence Ouverte 2.0) + Esri France/MRN
(diffusion, ODbL). France métropolitaine, hors ville de Paris (absence ≠ aléa nul).
"""

from __future__ import annotations

import json
import time
from typing import Any

from solveille.common import http
from solveille.common.config import get_settings
from solveille.common.logging import get_logger
from solveille.common.raw import RawDataset, write_manifest

log = get_logger("solveille.ingest.rga_2026")

SOURCE = "rga_2026"
# Service FeatureServer (nom encodé : « Géorisques » → G%C3%A9orisques).
FS_LAYER = (
    "https://services.arcgis.com/d3voDfTFbHOCRwVR/arcgis/rest/services/"
    "G%C3%A9orisques_Exposition_retrait_gonflement_des_argiles_2026/FeatureServer/2"
)
QUERY_URL = f"{FS_LAYER}/query"
PAGE = 2000  # = maxRecordCount du service

LICENCE = (
    "Zonage RGA 2026 (arrêté du 9 janv. 2026). Source : Géorisques / BRGM / MTE "
    "(Licence Ouverte 2.0). Diffusion via FeatureServer ArcGIS hébergé Esri France / MRN "
    "(ODbL) — voie de repli automatisable. Géométrie dissoute par (département × niveau)."
)


def _distinct_departements() -> list[str]:
    """Liste les codes département présents dans le service (pour le run national)."""
    params = {
        "where": "1=1",
        "outFields": "DPT",
        "returnDistinctValues": "true",
        "returnGeometry": "false",
        "orderByFields": "DPT",
        "f": "json",
    }
    data = http.get_json(QUERY_URL, params=params)
    return [
        a["DPT"]
        for f in data.get("features", [])
        if (a := f.get("attributes", {})) and a.get("DPT")
    ]


def _fetch_features(where: str) -> dict[str, Any]:
    """Récupère toutes les entités correspondant à `where` (pagination défensive)."""
    features: list[dict[str, Any]] = []
    offset = 0
    s = get_settings()
    while True:
        params = {
            "where": where,
            "outFields": "DPT,NIVEAU,ALEA",
            "returnGeometry": "true",
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": PAGE,
        }
        gj = http.get_json(QUERY_URL, params=params)
        batch = gj.get("features", []) or []
        features.extend(batch)
        exceeded = gj.get("exceededTransferLimit") or (gj.get("properties", {}) or {}).get(
            "exceededTransferLimit"
        )
        if not exceeded or not batch:
            break
        offset += len(batch)
        time.sleep(s.http_pause_s)
    return {"type": "FeatureCollection", "features": features}


def fetch() -> RawDataset:
    """Télécharge le zonage RGA 2026 (par département si bornage, sinon national)."""
    s = get_settings()
    root = s.source_raw_dir(SOURCE)
    root.mkdir(parents=True, exist_ok=True)
    deps = s.departements or _distinct_departements()  # national : dérive la liste des depts
    scopes = [(dd, f"DPT='{dd}'", f"{dd}.geojson") for dd in deps]

    files = []
    total = 0
    for label, where, fname in scopes:
        gj = _fetch_features(where)
        n = len(gj["features"])
        total += n
        dest = root / fname
        dest.write_text(json.dumps(gj), encoding="utf-8")
        files.append(dest)
        log.info("rga.fetch_scope", scope=label, n_features=n, path=str(dest))
        time.sleep(s.http_pause_s)

    manifest = write_manifest(
        SOURCE,
        root,
        source_url=QUERY_URL,
        srs="EPSG:4326",
        source_version="RGA 2026 (arrêté du 9 janvier 2026)",
        n_rows=total,
        files=files,
        extra={
            "bornage": deps or "national",
            "niveau_mapping": {"1": "Faible", "2": "Moyen", "3": "Fort"},
            "couverture": "France métropolitaine hors ville de Paris (75)",
            "geometrie": "dissoute par (département × niveau)",
            "licence": LICENCE,
        },
    )
    log.info("rga.done", n_features=total, files=[str(f) for f in files])
    return RawDataset(SOURCE, root, files, manifest)


def main() -> None:
    fetch()


if __name__ == "__main__":
    main()
