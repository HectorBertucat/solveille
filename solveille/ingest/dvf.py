"""Connecteur DVF géolocalisé (DGFiP / Etalab) — prix médian maison par commune.

Utilise l'arborescence stable `files.data.gouv.fr/geo-dvf/latest/csv/{année}/departements/`
(bornage natif par département, fichiers `.csv.gz`). Fenêtre récente (2023-2025) pour un
prix médian robuste. Alsace-Moselle (57/67/68) et Mayotte (976) sont **absents** (livre
foncier / droit local) → sautés proprement (`allow_missing`).

⚠️ **Légal (R112 A-3 LPF)** : seules des **agrégations communales** sont produites en aval
(`transform/commune_dvf`) — jamais de transaction nominative, pas de réidentification. Le
brut (avec adresses) reste en zone locale `data/raw/` non exposée.
"""

from __future__ import annotations

import time

from solveille.common import http
from solveille.common.config import get_settings
from solveille.common.logging import get_logger
from solveille.common.raw import RawDataset, write_manifest

log = get_logger("solveille.ingest.dvf")

SOURCE = "dvf"
BASE = "https://files.data.gouv.fr/geo-dvf/latest/csv"
#: Fenêtre récente (années glissantes disponibles : 2021-2025) pour le prix médian.
DVF_YEARS = (2023, 2024, 2025)


def _metro_departements() -> list[str]:
    """Départements métropolitains (01-95 hors 20, + 2A/2B). 57/67/68/976 sautés en aval."""
    deps = [f"{i:02d}" for i in range(1, 96) if i != 20]
    return sorted([*deps, "2A", "2B"])


def fetch() -> RawDataset:
    """Télécharge les CSV.gz DVF par (année, département) ; cache conditionnel, polis."""
    s = get_settings()
    deps = s.departements or _metro_departements()
    root = s.source_raw_dir(SOURCE)
    files = []
    missing = []
    for year in DVF_YEARS:
        for dd in deps:
            url = f"{BASE}/{year}/departements/{dd}.csv.gz"
            dest = root / str(year) / f"{dd}.csv.gz"
            result = http.download(url, dest, allow_missing=True)
            if result.status == "missing":
                missing.append(f"{year}/{dd}")
            else:
                files.append(dest)
            time.sleep(s.http_pause_s)
    log.info("dvf.fetched", n_files=len(files), n_missing=len(missing))
    manifest = write_manifest(
        SOURCE,
        root,
        source_url=f"{BASE}/<année>/departements/<dept>.csv.gz",
        srs="EPSG:4326",
        source_version=f"geo-dvf latest, années {min(DVF_YEARS)}-{max(DVF_YEARS)}",
        files=files,
        extra={
            "annees": list(DVF_YEARS),
            "departements_absents": missing,
            "note_absences": "Alsace-Moselle (57/67/68) et Mayotte (976) hors DVF (livre foncier)",
            "legal": "R112 A-3 LPF : agrégats communaux uniquement en aval, brut non exposé",
            "licence": "Licence Ouverte 2.0 (Etalab) — DGFiP, traitement Etalab/data.gouv",
        },
    )
    return RawDataset(SOURCE, root, files, manifest)


def main() -> None:
    ds = fetch()
    log.info("dvf.done", n_files=len(ds.files))


if __name__ == "__main__":
    main()
