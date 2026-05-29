"""Routes API : fiche commune et métadonnées de fraîcheur."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from solveille.api import deps

#: Cadrage produit (garde-fou : indice indicatif, pas un diagnostic).
DISCLAIMER = (
    "Indice territorial indicatif — ne prédit pas de fissures par bâtiment, "
    "n'est ni un diagnostic géotechnique ni un conseil d'achat/assurance."
)

router = APIRouter()


class CommunePression(BaseModel):
    """Fiche commune servie : exposition E, enjeu J, et pression IP-RGA du mois servi (v1)."""

    model_config = ConfigDict(extra="allow")

    insee: str
    nom: str | None = None
    code_dept: str | None = None
    date: str | None = None  # mois servi (AAAA-MM-JJ, 1er du mois)
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
    z_swi: float | None = None
    dry_swi: float | None = None
    T: float | None = None
    confiance_t: float | None = None
    ip_rga_score: float | None = None
    ip_rga_niveau: str | None = None
    ip_rga_niveau_code: int | None = None
    last_updated_swi: str | None = None


class SerieMois(BaseModel):
    """Un point de la série mensuelle de pression."""

    date_mois: str
    T: float | None = None
    ip_rga_score: int | None = None
    ip_rga_niveau: str | None = None
    ip_rga_niveau_code: int | None = None


class CommuneSerie(BaseModel):
    """Série temporelle de pression d'une commune (pour le sparkline du front)."""

    insee: str
    serie: list[SerieMois]


#: Mois au format AAAA-MM (curseur de date) ; validé côté requête.
_MOIS_RE = r"^\d{4}-\d{2}$"


@router.get("/communes/{insee}", response_model=CommunePression, tags=["communes"])
def get_commune(
    insee: str,
    mois: str | None = Query(None, pattern=_MOIS_RE, description="Mois AAAA-MM (défaut : dernier)"),
) -> CommunePression:
    """Fiche d'une commune par code INSEE (exposition, enjeu, pression IP-RGA du mois `mois`)."""
    row = deps.fetch_commune(insee, mois)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Commune {insee!r} inconnue")
    return CommunePression(**row)


@router.get("/communes/{insee}/serie", response_model=CommuneSerie, tags=["communes"])
def get_commune_serie(insee: str) -> CommuneSerie:
    """Série mensuelle de pression (T, score, niveau) d'une commune — alimente le sparkline."""
    serie = deps.fetch_serie(insee)
    # série vide + commune inexistante → 404 (sinon mart mensuel simplement absent → série vide)
    if not serie and deps.fetch_commune(insee) is None:
        raise HTTPException(status_code=404, detail=f"Commune {insee!r} inconnue")
    return CommuneSerie(insee=insee, serie=[SerieMois(**p) for p in serie])


@router.get("/meta", tags=["meta"])
def get_meta() -> dict[str, Any]:
    """Fraîcheur par source (`last_updated_*`), volumétrie servie et cadrage."""
    meta = deps.fetch_meta()
    meta["disclaimer"] = DISCLAIMER
    return meta
