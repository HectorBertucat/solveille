"""Orchestrateur du DAG de transformation (raw → staging → marts).

Idempotent : relancer recalcule les tables à partir du brut. Deux modes :
- `python -m solveille.transform.build` (défaut) : DAG **complet** (v0 statique + v1 SWI).
- `python -m solveille.transform.build swi` : **refresh SWI léger** (mensuel) — ne recalcule
  que la dynamique (staging SWI → mart), en **réutilisant** le staging v0 (commune_rga, _stock,
  _dvf…) et les poids spatiaux `commune_maille_poids` (statiques) → pas de re-fetch/recalcul
  RGA/DVF (politesse réseau + rapidité). Cf. `deploy/run-refresh.sh`, `make build-swi`.
"""

from __future__ import annotations

import sys

from solveille.common.config import get_settings
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


def build_all() -> None:
    """DAG complet : v0 (exposition/enjeu) + v1 (tension SWI) → marts."""
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


def refresh_swi() -> None:
    """Refresh SWI léger (mensuel) : réutilise le staging v0 + `commune_maille_poids`.

    Recalcule uniquement la chaîne dynamique (grille/maille/climatologie/anomalie → z_SWI
    communal → marts). Les poids spatiaux (statiques) sont reconstruits seulement s'ils manquent.
    """
    log.info("build.refresh_swi.start")
    staging.build_swi_grille()
    staging.build_swi_maille()
    staging.build_swi_clim()
    staging.build_swi_anomalie()
    if not (get_settings().staging_dir / "commune_maille_poids.parquet").exists():
        commune_swi.build_commune_maille_poids()  # 1er run / seed : poids spatiaux absents
    commune_swi.build_commune_swi()
    mart.build_commune_pression_mensuel()
    mart.build_commune_pression()
    log.info("build.refresh_swi.done")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "swi":
        refresh_swi()
    else:
        build_all()


if __name__ == "__main__":
    main()
