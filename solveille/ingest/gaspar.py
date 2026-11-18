"""Connecteur GASPAR — arrêtés Cat-Nat (DGPR/BRGM, Géorisques) → calibration `H`.

On ingère l'extrait CATNAT de la base nationale GASPAR via l'archive `gaspar.zip` (résolue
par l'API data.gouv, jeu `base-nationale-…-gaspar`, **Licence Ouverte 2.0**). On n'extrait
que `catnat_gaspar.csv` (1 ligne par commune × reconnaissance × aléa, **tous aléas**) ; le
filtrage sécheresse (`lib_risque_jo='Sécheresse'`) vit dans le staging
(`transform/staging.build_catnat_secheresse`) → la zone brute reste **immuable et complète**.

Schéma `catnat_gaspar.csv` (`;`, UTF-8, dates ISO `YYYY-MM-DD HH:MM:SS`), depuis 2026 :
`id_gaspar;code_commune;libelle_commune;num_risque_jo;lib_risque_jo;date_debut;date_fin;date_signature_arrete;date_publication_jo;date_modification`
(avant : `cod_nat_catnat;cod_commune;lib_commune;…;dat_deb;dat_fin;dat_pub_arrete;dat_pub_jo;`
`dat_maj`, mêmes colonnes dans le même ordre ; le staging lit les deux). Clé commune =
`code_commune` (INSEE, **texte** : zéros, Corse 2A/2B). `last_updated_gaspar` = `last_modified` de la
ressource. Cf. ADR-019, `docs/data-sources.md §6`.

Depuis l'automne 2026 le membre de l'archive est **daté** (`catnat_gaspar_2026-11-02.csv`) :
on le retrouve par motif et on le range sous le nom stable `catnat_gaspar.csv`.

L'API REST `…/api/v1/gaspar/catnat?code_insee=` (per-INSEE, **pas** de filtre département →
500) reste un repli ciblé pour un refresh à la commune, jamais le bulk national.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any

from solveille.common import datagouv, http
from solveille.common.config import get_settings
from solveille.common.logging import get_logger
from solveille.common.raw import RawDataset, write_manifest

log = get_logger("solveille.ingest.gaspar")

SOURCE = "gaspar"
DATASET = (
    "base-nationale-de-gestion-assistee-des-procedures-administratives-relatives-aux-risques-gaspar"
)
#: Nom stable de l'extrait CATNAT national dans la zone brute (lu par le staging).
CATNAT_MEMBER = "catnat_gaspar.csv"
#: Membre de l'archive : `catnat_gaspar.csv`, ou daté `catnat_gaspar_YYYY-MM-DD.csv`.
_CATNAT_MEMBER_RE = re.compile(r"(?:^|/)catnat_gaspar(?:_\d{4}-\d{2}-\d{2})?\.csv$", re.IGNORECASE)
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


def catnat_member(names: list[str]) -> str:
    """Nom du membre CATNAT parmi ceux de l'archive (le plus récent si plusieurs sont datés)."""
    found = sorted(n for n in names if _CATNAT_MEMBER_RE.search(n))
    if not found:
        raise FileNotFoundError(
            f"Aucun membre catnat_gaspar[_AAAA-MM-JJ].csv dans gaspar.zip (membres : {names})"
        )
    return found[-1]


def extract_catnat(zip_path: Path, root: Path) -> Path:
    """Extrait le membre CATNAT de `gaspar.zip` vers `root/catnat_gaspar.csv` (nom stable).

    Écrit dans un `.part` puis renommé : un extrait interrompu ne remplace jamais le précédent.
    """
    dest = root / CATNAT_MEMBER
    part = dest.with_name(dest.name + ".part")
    with zipfile.ZipFile(zip_path) as z:
        member = catnat_member(z.namelist())
        with z.open(member) as src, part.open("wb") as out:
            while chunk := src.read(1 << 20):
                out.write(chunk)
    part.replace(dest)
    log.info("gaspar.extract", member=member, path=str(dest))
    return dest


def fetch() -> RawDataset:
    """Télécharge `gaspar.zip` (GET conditionnel) et extrait `catnat_gaspar.csv` en brut."""
    s = get_settings()
    root = s.source_raw_dir(SOURCE)
    root.mkdir(parents=True, exist_ok=True)
    url, last_modified, resource_id = _resolve_zip()

    zip_dest = root / "gaspar.zip"
    result = http.download(url, zip_dest)  # conditional=True (ETag/Last-Modified) → idempotent
    log.info("gaspar.download", status=result.status, bytes=result.n_bytes, url=url)

    csv_path = extract_catnat(zip_dest, root)

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
