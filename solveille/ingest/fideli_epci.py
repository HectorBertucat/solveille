"""Connecteur Fideli/SDES — exposition des maisons individuelles au RGA, par EPCI.

Deux CSV (résolus via l'API data.gouv) : (1) maisons exposées **par période de
construction**, (2) **surfaces + maisons par EPCI** (format long). Maille **EPCI**
(SIREN 9 chiffres) — la descente à la commune est faite dans `transform/downscale_fideli`.

⚠️ Zonage sous-jacent = **exposition BRGM 2020** (pas le zonage RGA 2026) ; valeur littérale
`secret` (secret statistique) dans les colonnes nombre → gérée en `transform/` (→ NULL).
Séparateur `;`, UTF-8. Licence Ouverte (Etalab).
"""

from __future__ import annotations

from typing import Any

from solveille.common import datagouv, http
from solveille.common.config import get_settings
from solveille.common.logging import get_logger
from solveille.common.raw import RawDataset, write_manifest

log = get_logger("solveille.ingest.fideli_epci")

SOURCE = "fideli_epci"
SLUG = "exposition-des-maisons-individuelles-au-phenomene-de-retrait-gonflement-des-argiles-rga"


def _pick_csvs(ds: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Renvoie (ressource « par période », ressource « par EPCI/surfaces »)."""
    csvs = [r for r in ds.get("resources", []) if (r.get("format") or "").lower() == "csv"]
    epci = next((r for r in csvs if "surface" in (r.get("title") or "").lower()), None)
    periode = next((r for r in csvs if "surface" not in (r.get("title") or "").lower()), None)
    if epci is None or periode is None:
        raise LookupError("Les deux CSV Fideli (période / surfaces-EPCI) n'ont pas été résolus")
    return periode, epci


def fetch() -> RawDataset:
    """Télécharge les deux CSV Fideli (résolution dynamique des ressources)."""
    s = get_settings()
    ds = datagouv.dataset(SLUG)
    periode, epci = _pick_csvs(ds)
    root = s.source_raw_dir(SOURCE)
    files = []
    for res, fname in [(periode, "fideli_par_periode.csv"), (epci, "fideli_par_epci.csv")]:
        url = res.get("url") or datagouv.stable_resource_url(res["id"])
        dest = root / fname
        result = http.download(url, dest)
        files.append(dest)
        log.info("fideli.download", file=fname, status=result.status, bytes=result.n_bytes)
    manifest = write_manifest(
        SOURCE,
        root,
        source_url=f"https://www.data.gouv.fr/datasets/{SLUG}",
        srs=None,
        source_version=epci.get("last_modified"),
        files=files,
        extra={
            "maille": "EPCI (SIREN 9 chiffres)",
            "zonage_sous_jacent": "exposition BRGM 2020 (≠ zonage RGA 2026)",
            "secret_statistique": "valeur littérale 'secret' possible dans les colonnes nombre",
            "licence": "Licence Ouverte (Etalab) — SDES, d'après BRGM 2020 et Insee Fidéli 2021",
        },
    )
    return RawDataset(SOURCE, root, files, manifest)


def main() -> None:
    ds = fetch()
    log.info("fideli.done", files=[str(f) for f in ds.files])


if __name__ == "__main__":
    main()
