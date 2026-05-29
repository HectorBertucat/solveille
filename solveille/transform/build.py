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
    piezo,
    piezo_ips,
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
    # v1.1 — IPS piézométrique (raffinement local de T) : optionnel (skip si pas de fetch-piezo)
    build_piezo_chain()
    # marts : mensuel d'abord (fournit le dernier mois au statique)
    mart.build_commune_pression_mensuel()
    mart.build_commune_pression()
    log.info("build.done")


def build_piezo_chain() -> bool:
    """Chaîne IPS piézométrique (staging → IPS → `commune_ips`). **Optionnelle** : si le brut
    Hub'eau est absent (`make fetch-piezo` non lancé), on **skip** avec un avertissement — le
    SWI porte seul la boussole (couverture 100 %). Renvoie True si l'IPS a été construit."""
    try:
        piezo.build_piezo_stations()
        piezo.build_piezo_mensuel()
        piezo_ips.build_piezo_ips()
        piezo_ips.build_commune_ips()
    except FileNotFoundError as exc:
        log.warning("build.piezo_skip", reason=str(exc))  # IPS optionnel (raffinement local)
        return False
    return True


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


def refresh_piezo() -> None:
    """Refresh IPS léger (quotidien) : réutilise le staging v0 + SWI + climatologie SWI.

    Re-traite la chaîne piézo (staging stations/mensuel → IPS → `commune_ips`) puis les marts,
    en réutilisant `commune_swi` (mensuel, non recalculé ici). À enchaîner avec `make tiles`.
    Le brut piézo doit être rafraîchi avant (`make fetch-piezo`, incrémental). Skip propre si
    le brut est absent.
    """
    log.info("build.refresh_piezo.start")
    if not build_piezo_chain():
        log.warning("build.refresh_piezo.no_raw")  # pas de fetch-piezo → rien à faire
        return
    mart.build_commune_pression_mensuel()
    mart.build_commune_pression()
    log.info("build.refresh_piezo.done")


def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "swi":
        refresh_swi()
    elif arg == "piezo":
        refresh_piezo()
    else:
        build_all()


if __name__ == "__main__":
    main()
