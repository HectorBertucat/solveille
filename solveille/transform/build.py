"""Orchestrateur du DAG de transformation (raw → staging → marts).

Idempotent : relancer recalcule les tables à partir du brut. Modes :
- `python -m solveille.transform.build` (défaut) : DAG **complet** (v0 statique + v1 SWI + v2 H).
- `… build swi` : **refresh SWI léger** (mensuel) — ne recalcule que la dynamique (staging SWI →
  catnat/H → mart), en **réutilisant** le staging v0 (commune_rga, _stock, _dvf…) et les poids
  spatiaux `commune_maille_poids` (statiques). Cf. `deploy/run-refresh.sh`, `make build-swi`.
- `… build piezo` : **refresh IPS léger** (quotidien) — piézo → `commune_ips` → mart (relit
  `commune_h` s'il existe, ne le reconstruit pas). Cf. `deploy/run-refresh-piezo.sh`.
- `… build gaspar` : **refresh GASPAR léger** (hebdo) — `catnat_secheresse` + `commune_h` (réutilise
  `commune_swi_hist`) → mart. Cf. `deploy/run-refresh-gaspar.sh`, `make build-gaspar`.
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
    h_calib,
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
    # v2 — GASPAR Cat-Nat sécheresse (substrat calibration H) : optionnel (skip sans fetch-gaspar)
    build_catnat_chain()
    build_h_chain()  # calibration H (commune_swi_hist + commune_h) : optionnel (skip sans catnat)
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


def build_catnat_chain() -> bool:
    """Staging GASPAR sécheresse → `catnat_secheresse` (substrat de calibration `H`).
    **Optionnel** : si le brut GASPAR est absent (`make fetch-gaspar` non lancé), on **skip**
    avec un avertissement — `H` sera simplement indisponible. Renvoie True si construit."""
    try:
        staging.build_catnat_secheresse()
    except FileNotFoundError as exc:
        log.warning("build.catnat_skip", reason=str(exc))  # H optionnel (calibration v2)
        return False
    return True


def build_h_chain() -> bool:
    """Calibration `H` : substrat `z_SWI` historique + `commune_h`. **Optionnelle** : nécessite
    `catnat_secheresse` (GASPAR) — skip propre sinon. Renvoie True si construit."""
    if not (get_settings().staging_dir / "catnat_secheresse.parquet").exists():
        log.warning("build.h_skip", reason="catnat_secheresse absent")  # H optionnel
        return False
    h_calib.build_commune_swi_hist()
    h_calib.build_commune_h()
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
    build_catnat_chain()  # GASPAR sécheresse (optionnel) : recalcule l'agrégat par commune
    build_h_chain()  # calibration H (substrat historique + commune_h) : optionnel
    mart.build_commune_pression_mensuel()
    mart.build_commune_pression()
    log.info("build.refresh_swi.done")


def refresh_gaspar() -> None:
    """Refresh GASPAR **hebdomadaire** : recalcule `catnat_secheresse` + `commune_h` + marts.

    Le brut GASPAR doit être rafraîchi avant (`make fetch-gaspar`). **Réutilise** le substrat
    `z_SWI` historique (`commune_swi_hist`, qui ne dépend que du SWI mensuel) s'il existe — on
    ne le reconstruit qu'au 1er run. Skip propre si le brut GASPAR est absent. À enchaîner avec
    `make tiles` (les seuils de niveaux sont recalculés au rebuild du mart mensuel)."""
    log.info("build.refresh_gaspar.start")
    try:
        staging.build_catnat_secheresse()
    except FileNotFoundError as exc:
        log.warning("build.refresh_gaspar.no_raw", reason=str(exc))  # pas de fetch-gaspar
        return
    if not (get_settings().staging_dir / "commune_swi_hist.parquet").exists():
        h_calib.build_commune_swi_hist()  # 1er run / seed : substrat historique absent
    h_calib.build_commune_h()
    mart.build_commune_pression_mensuel()
    mart.build_commune_pression()
    log.info("build.refresh_gaspar.done")


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
    elif arg == "gaspar":
        refresh_gaspar()
    else:
        build_all()


if __name__ == "__main__":
    main()
