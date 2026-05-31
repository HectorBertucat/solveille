"""Connecteur GASPAR — arrêtés Cat-Nat (DGPR/BRGM, Géorisques) → calibration `H`.

On ingère l'extrait CATNAT de la base nationale GASPAR via l'archive `gaspar.zip` (résolue
par l'API data.gouv, jeu `base-nationale-…-gaspar`, **Licence Ouverte 2.0**). On n'extrait
que `catnat_gaspar.csv` (1 ligne par commune × reconnaissance × aléa, **tous aléas**) ; le
filtrage sécheresse (`lib_risque_jo='Sécheresse'`) vit dans le staging
(`transform/staging.build_catnat_secheresse`) → la zone brute reste **immuable et complète**.

Schéma `catnat_gaspar.csv` (`;`, UTF-8, dates ISO `YYYY-MM-DD HH:MM:SS`) :
`cod_nat_catnat;cod_commune;lib_commune;num_risque_jo;lib_risque_jo;dat_deb;dat_fin;dat_pub_arrete;dat_pub_jo;dat_maj`.
Clé commune = `cod_commune` (INSEE, **texte** : zéros, Corse 2A/2B). `last_updated_gaspar`
= `last_modified` de la ressource. Cf. ADR-019, `docs/data-sources.md §6`.

L'API REST `…/api/v1/gaspar/catnat?code_insee=` (per-INSEE, **pas** de filtre département →
500) reste un repli ciblé pour un refresh à la commune, jamais le bulk national.
"""

from __future__ import annotations

from typing import Any

from solveille.common import datagouv, http
from solveille.common.archive import extract_zip
from solveille.common.config import get_settings
from solveille.common.logging import get_logger
from solveille.common.raw import RawDataset, write_manifest

log = get_logger("solveille.ingest.gaspar")

SOURCE = "gaspar"
DATASET = (
    "base-nationale-de-gestion-assistee-des-procedures-administratives-relatives-aux-risques-gaspar"
)
#: Membre de l'archive qui nous intéresse (extrait CATNAT national).
CATNAT_MEMBER = "catnat_gaspar.csv"
#: URL directe de repli si l'API data.gouv ne résout pas la ressource (Géorisques/DGPR).
FALLBACK_ZIP_URL = "https://files.georisques.fr/GASPAR/gaspar.zip"


def _resolve_zip() -> tuple[str, str | None, str | None]:
    """Résout `(url, last_modified, resource_id)` de `gaspar.zip` via l'API data.gouv.

    Repli sur l'URL directe Géorisques si l'API échoue ou ne référence pas l'archive
    (UUID/URL de ressource pouvant changer — on échoue mou, jamais en silence).
    """
    try:
        ds = datagouv.dataset(DATASET)
        for r in ds.get("resources", []):
            url = (r.get("url") or "").strip()
            if url.lower().endswith("gaspar.zip"):
                return url, r.get("last_modified"), r.get("id")
        log.warning("gaspar.resource_not_found", dataset=DATASET)  # → repli URL directe
    except Exception as exc:  # API down / schéma changé → repli URL directe
        log.warning("gaspar.datagouv_failed", error=str(exc))
    return FALLBACK_ZIP_URL, None, None


def fetch() -> RawDataset:
    """Télécharge `gaspar.zip` (GET conditionnel) et extrait `catnat_gaspar.csv` en brut."""
    s = get_settings()
    root = s.source_raw_dir(SOURCE)
    root.mkdir(parents=True, exist_ok=True)
    url, last_modified, resource_id = _resolve_zip()

    zip_dest = root / "gaspar.zip"
    result = http.download(url, zip_dest)  # conditional=True (ETag/Last-Modified) → idempotent
    log.info("gaspar.download", status=result.status, bytes=result.n_bytes, url=url)

    extracted = extract_zip(zip_dest, root, suffixes=(CATNAT_MEMBER,))
    csv_path = extracted[0]
    log.info("gaspar.extract", member=csv_path.name, path=str(csv_path))

    extra: dict[str, Any] = {
        "dataset": DATASET,
        "resource_id": resource_id,
        "archive": zip_dest.name,
        "member": csv_path.name,
        "download_status": result.status,
        "licence": "Licence Ouverte 2.0 — Géorisques / GASPAR (DGPR/BRGM, MTE)",
        "note_filtrage": "Sécheresse (lib_risque_jo='Sécheresse') filtrée en staging.",
    }
    manifest = write_manifest(
        SOURCE,
        root,
        source_url=url,
        srs=None,  # attributaire (clé INSEE), pas de géométrie
        source_version=last_modified or result.last_modified,
        files=[csv_path],
        extra=extra,
    )
    return RawDataset(SOURCE, root, [csv_path], manifest)


def main() -> None:
    ds = fetch()
    log.info("gaspar.done", files=[str(f) for f in ds.files])


if __name__ == "__main__":
    main()
