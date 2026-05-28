"""Résolution de la dernière ressource d'un jeu data.gouv.fr via l'API publique.

On évite de coder une URL de ressource en dur : les liens changent, mais l'API
`/api/1/datasets/<id>/` et le redirecteur stable `/api/1/datasets/r/<resource_id>`
restent valides.
"""

from __future__ import annotations

from typing import Any

from solveille.common import http

API = "https://www.data.gouv.fr/api/1"


def dataset(dataset_id: str) -> dict[str, Any]:
    """Métadonnées d'un jeu de données (par id ou slug)."""
    return http.get_json(f"{API}/datasets/{dataset_id}/")


def stable_resource_url(resource_id: str) -> str:
    """Lien stable (302 vers le fichier courant) d'une ressource."""
    return f"{API}/datasets/r/{resource_id}"


def pick_resource(
    ds: dict[str, Any],
    *,
    fmt: str | None = None,
    title_contains: str | None = None,
) -> dict[str, Any]:
    """Sélectionne une ressource (filtre format/titre), la plus récente d'abord.

    Lève `LookupError` si aucune ressource ne correspond — on échoue clairement
    plutôt que de produire des données fausses.
    """
    candidates: list[dict[str, Any]] = []
    for r in ds.get("resources", []):
        if fmt and (r.get("format") or "").lower() != fmt.lower():
            continue
        if title_contains and title_contains.lower() not in (r.get("title") or "").lower():
            continue
        candidates.append(r)
    if not candidates:
        raise LookupError(
            f"Aucune ressource (format={fmt!r}, titre~{title_contains!r}) "
            f"dans le jeu {ds.get('id') or ds.get('slug')!r}"
        )
    candidates.sort(key=lambda r: r.get("last_modified") or "", reverse=True)
    return candidates[0]
