"""Orchestrateur du DAG de transformation (raw → staging → marts).

Idempotent : relancer recalcule les tables à partir du brut. Les étapes sont ajoutées
au fil de la roadmap (v0 : commune → commune_rga → … → commune_pression).
"""

from __future__ import annotations

from solveille.common.logging import get_logger
from solveille.transform import (
    commune_dvf,
    commune_rga,
    commune_swi,
    downscale_fideli,
    mart,
    staging,
)

log = get_logger("solveille.transform.build")


def main() -> None:
    log.info("build.start")
    # v0 — exposition / enjeu (statique)
    staging.build_commune()
    staging.build_rga()
    commune_rga.build_commune_rga()
    staging.build_commune_bascule()
    staging.build_commune_logement()
    staging.build_epci_stock()
    staging.build_epci_stock_periode()
    downscale_fideli.build_commune_stock()
    commune_dvf.build_commune_dvf()
    # v1 — tension hydrique SWI (dynamique)
    staging.build_swi_grille()
    staging.build_swi_maille()
    staging.build_swi_clim()
    staging.build_swi_anomalie()
    commune_swi.build_commune_maille_poids()
    commune_swi.build_commune_swi()
    # marts : mensuel d'abord (fournit le dernier mois au statique)
    mart.build_commune_pression_mensuel()
    mart.build_commune_pression()
    log.info("build.done")


if __name__ == "__main__":
    main()
