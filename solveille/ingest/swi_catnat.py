"""Connecteur SWI CatNat (Météo-France) — indice mensuel d'humidité des sols pour le
dispositif catastrophes naturelles, support de la tension hydrique `T`.

Voie d'acquisition (ADR-015) : le **bon** jeu data.gouv est `…-catnat`
(id `69380f267975cac439339b63`) — PAS l'homonyme `…-catastrophes-naturelles`, qui est
mono-ressource et redirige (302) vers un portail JS Météo-France non automatisable. Le bon
jeu sert directement **7 CSV.gz décennaux** (`swi.196001-196912` … `swi.202001-2025xx`) +
un **fichier grille** (centroïdes maille en L93), via des URLs `latest`
(`/api/1/datasets/r/<uuid>`, 302 → CDN `static.data.gouv.fr`).

Connecteur **poli/idempotent** : on résout les ressources via l'API dataset (UUIDs en repli
si l'API change), puis on télécharge via `http.download` (cache conditionnel ETag/
Last-Modified). ~8 requêtes/mois ; seul le fichier de la décennie courante évolue. Les
coordonnées `LAMBX`/`LAMBY` des fichiers SWI sont **déjà en EPSG:2154** (mètres) → pas de
reprojection. Licence Ouverte 2.0, Météo-France.
"""

from __future__ import annotations

import os
import time
from typing import Any

from solveille.common import http
from solveille.common.config import get_settings
from solveille.common.logging import get_logger
from solveille.common.raw import RawDataset, write_manifest

log = get_logger("solveille.ingest.swi_catnat")

SOURCE = "swi_catnat"
DATASET_ID = "69380f267975cac439339b63"
DATASET_API = f"https://www.data.gouv.fr/api/1/datasets/{DATASET_ID}/"


def _latest(uuid: str) -> str:
    return f"https://www.data.gouv.fr/api/1/datasets/r/{uuid}"


#: Repli si l'API dataset est indisponible/modifiée (UUIDs `latest`, stables — résolvent la
#: dernière édition côté CDN). Vérifiés 2026-05 (cf. ADR-015).
FALLBACK_DECADES: dict[str, str] = {
    "swi.196001-196912.csv.gz": _latest("d0893c8f-0c99-4b4f-9fe7-d6c09f8d0259"),
    "swi.197001-197912.csv.gz": _latest("d162bab9-4ff6-4651-b84d-a9aa9dbb2523"),
    "swi.198001-198912.csv.gz": _latest("7b9891b1-b27b-4b82-8393-d8f93fce2618"),
    "swi.199001-199912.csv.gz": _latest("2e334b50-3e44-4c76-a7c1-ea48af68a573"),
    "swi.200001-200912.csv.gz": _latest("cee37a28-3ba4-4a02-b841-dbbd5ae47437"),
    "swi.201001-201912.csv.gz": _latest("21e46f33-9488-44d0-9d2f-58c640e919b1"),
    "swi.202001-202512.csv.gz": _latest("eec92fc2-50f4-4250-8eb8-f126f9b36bc4"),
}
FALLBACK_GRILLE_URL = _latest("77383638-a25b-48f5-ba4d-b6dd3b0eae56")
#: Nom de fichier local stable pour la grille (le basename CDN porte un horodatage variable).
GRILLE_FILENAME = "grille_mailles.csv"

LICENCE = (
    "SWI uniforme mensuel pour le dispositif CatNat. Source : Météo-France "
    "(Licence Ouverte 2.0, Etalab), via data.gouv.fr (jeu …-catnat)."
)


def _classify(resource: dict[str, Any]) -> tuple[str, str] | None:
    """Mappe une ressource data.gouv → (nom de fichier local, URL de téléchargement),
    ou `None` si la ressource n'est pas à ingérer (PDF de présentation…).

    Télécharge via le `latest` (302 → CDN, suit le dernier millésime) ; nomme le fichier
    SWI par le basename CDN (`swi.<range>.csv.gz`, stable pour le glob staging) et la grille
    par un nom fixe.
    """
    title = (resource.get("title") or "").lower()
    url = resource.get("url") or ""
    fmt = (resource.get("format") or "").lower()
    download = resource.get("latest") or url
    base = os.path.basename(url.split("?", 1)[0])
    if "caracteristiques-geographiques-mailles" in title or "mailles-swi" in base:
        return GRILLE_FILENAME, download
    if base.startswith("swi.") and base.endswith(".csv.gz"):
        return base, download
    if fmt == "csv.gz" and "swi" in title:  # filet de sécurité si le basename change
        name = title.removesuffix(".csv").removesuffix(".gz") + ".csv.gz"
        return name, download
    return None  # PDF de présentation, etc.


def _resolve_resources() -> tuple[list[tuple[str, str]], str | None]:
    """Résout (nom_local, url) des fichiers à télécharger via l'API dataset ; repli sur les
    UUIDs figés. Renvoie aussi le `last_modified` du jeu (traçabilité publication)."""
    try:
        data = http.get_json(DATASET_API)
        resources = data.get("resources", []) or []
        targets = [c for r in resources if (c := _classify(r))]
        if targets:
            return targets, data.get("last_modified")
        log.warning("swi.api_empty_targets")  # structure inattendue → repli
    except Exception as exc:  # réseau / JSON / structure → repli sur les UUIDs connus
        log.warning("swi.api_resolve_failed", error=str(exc))
    fallback = [*FALLBACK_DECADES.items(), (GRILLE_FILENAME, FALLBACK_GRILLE_URL)]
    return fallback, None


def fetch() -> RawDataset:
    """Télécharge les fichiers SWI (décennies + grille) dans `data/raw/swi_catnat/`."""
    s = get_settings()
    root = s.source_raw_dir(SOURCE)
    root.mkdir(parents=True, exist_ok=True)
    targets, last_modified = _resolve_resources()

    files = []
    for name, url in targets:
        dest = root / name
        result = http.download(url, dest)
        files.append(dest)
        log.info("swi.fetch_file", name=name, status=result.status, bytes=result.n_bytes)
        time.sleep(s.http_pause_s)

    manifest = write_manifest(
        SOURCE,
        root,
        source_url=DATASET_API,
        srs="EPSG:2154",
        source_version=f"data.gouv …-catnat (id {DATASET_ID})",
        files=files,
        extra={
            "dataset_id": DATASET_ID,
            "dataset_last_modified": last_modified,
            "n_fichiers": len(files),
            "grille": GRILLE_FILENAME,
            "note_format": "CSV.gz ';' décimal '.' ; date AAAAMM ; LAMBX/LAMBY déjà en L93 (m)",
            "note_swi": "valeur mensuelle = moyenne glissante 3 mois ; échelle ~0-1 non bornée",
            "licence": LICENCE,
        },
    )
    log.info("swi.done", n_files=len(files), last_modified=last_modified)
    return RawDataset(SOURCE, root, files, manifest)


def main() -> None:
    fetch()


if __name__ == "__main__":
    main()
