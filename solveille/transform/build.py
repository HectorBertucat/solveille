"""Orchestrateur du DAG de transformation (raw → staging → marts).

Idempotent : relancer recalcule les tables à partir du brut. Les étapes sont ajoutées
au fil de la roadmap (v0 : commune → commune_rga → … → commune_pression).
"""

from __future__ import annotations

from solveille.common.logging import get_logger
from solveille.transform import commune_rga, staging

log = get_logger("solveille.transform.build")


def main() -> None:
    log.info("build.start")
    staging.build_commune()
    staging.build_rga()
    commune_rga.build_commune_rga()
    staging.build_commune_bascule()
    staging.build_commune_logement()
    # À venir (v0) : epci_stock, downscale_fideli, commune_dvf, mart commune_pression.
    log.info("build.done")


if __name__ == "__main__":
    main()
