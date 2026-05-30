#!/usr/bin/env bash
# Rafraîchissement quotidien IPS nappes : ré-ingestion Hub'eau **incrémentale** (stations +
# incrément de chroniques par code_bss, idempotent/poli), refresh IPS **léger** (réutilise le
# staging v0 + SWI ; recalcule piézo → commune_ips → marts) puis régénération des PMTiles.
# Verrou flock PARTAGÉ avec le refresh SWI (tous deux écrivent marts/tuiles) : si l'un tourne,
# l'autre sort proprement. Appelé par solveille-piezo.service (déclenché par solveille-piezo.timer).
set -euo pipefail
cd "${SOLVEILLE_REPO:-/opt/solveille}"
export PATH="/usr/local/bin:${HOME:-/root}/.local/bin:$PATH"
exec flock -n /tmp/solveille-refresh.lock make fetch-piezo build-piezo tiles
