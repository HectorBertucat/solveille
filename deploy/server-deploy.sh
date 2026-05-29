#!/usr/bin/env bash
# Déploiement côté serveur — appelé par la CI après `git reset --hard origin/main`.
# Met à jour les dépendances et redémarre l'API. **Ne rebuild PAS les données** (marts/PMTiles) :
# le rafraîchissement SWI mensuel est géré par solveille-swi.timer (deploy/run-refresh.sh).
set -euo pipefail
cd "${SOLVEILLE_REPO:-/opt/solveille}"
export PATH="/usr/local/bin:${HOME:-/root}/.local/bin:$PATH"

uv sync --locked
systemctl restart solveille-api
sleep 2
if systemctl is-active --quiet solveille-api; then
  echo "solveille-api: actif ✓"
else
  echo "solveille-api: ÉCHEC ✗" >&2
  journalctl -u solveille-api -n 30 --no-pager >&2 || true
  exit 1
fi
