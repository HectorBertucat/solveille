#!/usr/bin/env bash
# Rafraîchissement hebdomadaire GASPAR (calibration H) : ré-ingestion de l'archive nationale
# gaspar.zip (GET conditionnel, on n'extrait que catnat_gaspar.csv), refresh GASPAR **léger**
# (recalcule catnat_secheresse + commune_h en réutilisant le substrat z_SWI historique, puis
# les marts) et régénération des PMTiles (les seuils de niveaux sont recalculés au rebuild).
# Verrou flock PARTAGÉ avec les refreshs SWI/IPS (tous écrivent marts/tuiles) : si l'un tourne,
# l'autre sort proprement. Appelé par solveille-gaspar.service (déclenché par solveille-gaspar.timer).
set -euo pipefail
cd "${SOLVEILLE_REPO:-/opt/solveille}"
export PATH="/usr/local/bin:${HOME:-/root}/.local/bin:$PATH"
exec flock -n /tmp/solveille-refresh.lock make fetch-gaspar build-gaspar tiles
