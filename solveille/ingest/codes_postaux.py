"""Connecteur Base officielle des codes postaux (La Poste) → enrichit la recherche commune.

On ingère le CSV unique « base officielle des codes postaux » publié par **La Poste** sur
data.gouv.fr (**Licence Ouverte 2.0**), résolu par l'API data.gouv (jeu
`base-officielle-des-codes-postaux`, ressource CSV). 1 ligne = (commune INSEE × code postal ×
lieu-dit `Ligne_5`) → la dédup (insee × cp) et l'agrégation `cp[]` par commune vivent dans
`transform/build_search` (la zone brute reste immuable et complète).

Schéma CSV (`;`, UTF-8) — **piège : l'en-tête est préfixée d'un `#`** :
`#Code_commune_INSEE;Nom_de_la_commune;Code_postal;Libellé_d_acheminement;Ligne_5`.
Clé commune = `Code_commune_INSEE` (**texte** : zéros de tête, Corse 2A/2B ; jamais `CAST int`).
PLM : Paris `75056`→[75001..75020], Lyon `69123`, Marseille `13055` (pas de ligne par
arrondissement) → la jointure sur l'INSEE COG de `commune.parquet` fonctionne directement.
MAJ **semestrielle** → `last_updated_cp` = `last_modified` ressource. Cf. docs/data-sources.md §10.
"""

from __future__ import annotations

from typing import Any

from solveille.common import datagouv, http
from solveille.common.config import get_settings
from solveille.common.logging import get_logger
from solveille.common.raw import RawDataset, write_manifest

log = get_logger("solveille.ingest.codes_postaux")

SOURCE = "laposte_codes_postaux"
DATASET = "base-officielle-des-codes-postaux"
#: Identifiant de ressource stable (302 vers le CSV courant) — repli si l'API ne résout pas.
FALLBACK_RESOURCE_ID = "008a2dda-2c60-4b63-b910-998f6f818089"
#: Nom du fichier brut (un seul CSV, pas d'archive).
CSV_NAME = "codes_postaux.csv"


def _resolve_csv() -> tuple[str, str | None, str | None]:
    """Résout `(url, last_modified, resource_id)` du CSV via l'API data.gouv.

    Repli sur le redirecteur stable `/datasets/r/<id>` si l'API échoue ou ne référence pas de
    ressource CSV (on échoue mou, jamais en silence)."""
    try:
        ds = datagouv.dataset(DATASET)
        r = datagouv.pick_resource(ds, fmt="csv")
        url = (r.get("url") or "").strip() or datagouv.stable_resource_url(r["id"])
        return url, r.get("last_modified"), r.get("id")
    except Exception as exc:  # API down / schéma changé → repli redirecteur stable
        log.warning("codes_postaux.datagouv_failed", error=str(exc))
        return datagouv.stable_resource_url(FALLBACK_RESOURCE_ID), None, FALLBACK_RESOURCE_ID


def fetch() -> RawDataset:
    """Télécharge le CSV des codes postaux (GET conditionnel) en zone brute."""
    s = get_settings()
    root = s.source_raw_dir(SOURCE)
    root.mkdir(parents=True, exist_ok=True)
    url, last_modified, resource_id = _resolve_csv()

    csv_dest = root / CSV_NAME
    result = http.download(url, csv_dest)  # conditional=True (ETag/Last-Modified) → idempotent
    log.info("codes_postaux.download", status=result.status, bytes=result.n_bytes, url=url)

    extra: dict[str, Any] = {
        "dataset": DATASET,
        "resource_id": resource_id,
        "download_status": result.status,
        "licence": "Licence Ouverte 2.0 — La Poste (Base officielle des codes postaux)",
        "note_format": "CSV ';' UTF-8 ; en-tête préfixée '#' ; 1 ligne = (INSEE × CP × Ligne_5).",
    }
    manifest = write_manifest(
        SOURCE,
        root,
        source_url=url,
        srs=None,  # attributaire (clé INSEE + CP), pas de géométrie
        source_version=last_modified or result.last_modified,
        files=[csv_dest],
        extra=extra,
    )
    return RawDataset(SOURCE, root, [csv_dest], manifest)


def main() -> None:
    ds = fetch()
    log.info("codes_postaux.done", files=[str(f) for f in ds.files])


if __name__ == "__main__":
    main()
