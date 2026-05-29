#!/usr/bin/env bash
# Rafraîchissement mensuel SWI : ré-ingestion de la décennie courante (idempotent, ~8 GET),
# refresh SWI **léger** (réutilise le staging v0 + poids spatiaux, pas de re-fetch RGA/DVF —
# politesse réseau) puis régénération des PMTiles.
# Verrou anti-chevauchement (flock) : si un run précédent tourne encore, on sort proprement.
# Appelé par solveille-swi.service (déclenché par solveille-swi.timer).
set -euo pipefail
cd "${SOLVEILLE_REPO:-/opt/solveille}"
export PATH="/usr/local/bin:${HOME:-/root}/.local/bin:$PATH"
exec flock -n /tmp/solveille-refresh.lock make fetch-swi build-swi tiles
