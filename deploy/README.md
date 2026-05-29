# Déploiement — timers systemd

Artefacts de déploiement (VM Ubuntu, cf. `docs/architecture.md`). **Non exécutés par le repo** :
à installer sur la VM. Idempotents et polis (cache + bornage côté connecteurs).

## SWI mensuel (v1.0)

`solveille-swi.timer` → `solveille-swi.service` → `deploy/run-refresh.sh` :
`make fetch-swi build tiles` (ré-ingestion de la décennie courante, recalcul du mart, PMTiles),
sous verrou `flock` (anti-chevauchement).

### Installation

```bash
# 1. Code en /opt/solveille (ou adapter SOLVEILLE_REPO + WorkingDirectory dans le .service)
sudo useradd --system --home /opt/solveille solveille     # ou un user dédié existant
sudo chown -R solveille:solveille /opt/solveille
chmod +x /opt/solveille/deploy/run-refresh.sh

# 2. Installer les unités
sudo cp /opt/solveille/deploy/systemd/solveille-swi.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now solveille-swi.timer

# 3. Vérifier
systemctl list-timers solveille-swi.timer
sudo systemctl start solveille-swi.service   # lancement manuel (test)
journalctl -u solveille-swi.service -f
```

Cadence : le 5 de chaque mois à 04:00 (`OnCalendar`), `Persistent=true` rattrape un run manqué.

## Nappes quotidiennes (v1.1)

Le timer quotidien Hub'eau (`fetch-piezo`) sera ajouté avec l'IPS (v1.1) : `solveille-piezo.timer`
(quotidien) + recalcul incrémental de la valeur courante. Voir `docs/roadmap.md`.
