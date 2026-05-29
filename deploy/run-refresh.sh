#!/usr/bin/env bash
# Rafraîchissement mensuel SWI : ré-ingestion de la décennie courante (idempotent, ~8 GET),
# recalcul du mart (commune_pression_mensuel + statique) puis régénération des PMTiles.
# Verrou anti-chevauchement (flock) : si un run précédent tourne encore, on sort proprement.
# Appelé par solveille-swi.service (déclenché par solveille-swi.timer).
set -euo pipefail
cd "${SOLVEILLE_REPO:-/opt/solveille}"
exec flock -n /tmp/solveille-refresh.lock make fetch-swi build tiles
