"""Orchestrateur du DAG de transformation (raw → staging → marts).

Idempotent : relancer recalcule les tables à partir du brut. Les étapes sont ajoutées
au fil de la roadmap (v0 : commune → commune_rga → … → commune_pression).
"""

from __future__ import annotations

from solveille.common.logging import get_logger
from solveille.transform import commune_dvf, commune_rga, downscale_fideli, staging

log = get_logger("solveille.transform.build")


def main() -> None:
    log.info("build.start")
    staging.build_commune()
    staging.build_rga()
    commune_rga.build_commune_rga()
    staging.build_commune_bascule()
    staging.build_commune_logement()
    staging.build_epci_stock()
    staging.build_epci_stock_periode()
    downscale_fideli.build_commune_stock()
    commune_dvf.build_commune_dvf()
    # À venir (v0) : mart commune_pression.
    log.info("build.done")


if __name__ == "__main__":
    main()
