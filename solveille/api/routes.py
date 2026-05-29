"""Routes API : fiche commune et métadonnées de fraîcheur."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from solveille.api import deps

#: Cadrage produit (garde-fou : indice indicatif, pas un diagnostic).
DISCLAIMER = (
    "Indice territorial indicatif — ne prédit pas de fissures par bâtiment, "
    "n'est ni un diagnostic géotechnique ni un conseil d'achat/assurance."
)

router = APIRouter()


class CommunePression(BaseModel):
    """Fiche commune servie (E et J en v0 ; T/score = NULL jusqu'en v1)."""

    model_config = ConfigDict(extra="allow")

    insee: str
    nom: str | None = None
    code_dept: str | None = None
    E: float | None = None
    part_alea_moyen: float | None = None
    part_alea_fort: float | None = None
    part_alea_moyen_fort: float | None = None
    classe_dominante: str | None = None
    has_rga_coverage: bool | None = None
    part_maisons_vulnerables: float | None = None
    n_maisons_exposees: float | None = None
    valeur_bati_exposee_eur: float | None = None
    prix_median_maison_eur_m2: float | None = None
    surface_mediane_maison_m2: float | None = None
    n_tx_maison_12m: int | None = None
    basculement_2026: bool | None = None
    rga_classe_2020: int | None = None
    rga_classe_2026: int | None = None
    bascule_type: str | None = None
    ip_rga_score: float | None = None
    ip_rga_niveau: str | None = None


@router.get("/communes/{insee}", response_model=CommunePression, tags=["communes"])
def get_commune(insee: str) -> CommunePression:
    """Fiche d'une commune par code INSEE (exposition, enjeu, flag reclassement 2026)."""
    row = deps.fetch_commune(insee)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Commune {insee!r} inconnue")
    return CommunePression(**row)


@router.get("/meta", tags=["meta"])
def get_meta() -> dict[str, Any]:
    """Fraîcheur par source (`last_updated_*`), volumétrie servie et cadrage."""
    meta = deps.fetch_meta()
    meta["disclaimer"] = DISCLAIMER
    return meta
