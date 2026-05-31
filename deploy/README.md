# Déploiement — VM Ubuntu (Caddy + Cloudflare Tunnel) & CI/CD

VM partagée (cf. `docs/architecture.md`) : chaque app fournit son `deploy/Caddyfile` (importé par
`/etc/caddy/Caddyfile`), tourne en service systemd, et est exposée via **Cloudflare Tunnel**.
Solveille suit ce modèle **sans toucher aux autres apps**.

Composants :
- `solveille-api.service` — uvicorn (FastAPI) sur `127.0.0.1:8001`, sert API + front + PMTiles.
- `deploy/Caddyfile` — bloc `:8083` → `reverse_proxy localhost:8001` (importé par le Caddyfile global).
- `solveille-swi.{service,timer}` — refresh SWI **mensuel** (`deploy/run-refresh.sh` = `make fetch-swi build-swi tiles`, sous `flock`).
- `solveille-piezo.{service,timer}` — refresh IPS nappes **quotidien** (`deploy/run-refresh-piezo.sh` = `make fetch-piezo build-piezo tiles`, **flock partagé** avec le refresh SWI car tous deux écrivent marts/tuiles). Fetch Hub'eau **incrémental** (le 1er run, national, télécharge tout l'historique et peut être long ; les suivants ne récupèrent que l'incrément). Borné par `SOLVEILLE_DEPARTEMENTS` (vide = national).
- `solveille-gaspar.{service,timer}` — refresh GASPAR **hebdomadaire** (calibration `H`, v2) : `deploy/run-refresh-gaspar.sh` = `make fetch-gaspar build-gaspar tiles`, **flock partagé**. `build-gaspar` recalcule `catnat_secheresse` + `commune_h` (réutilise le substrat `z_SWI` historique `commune_swi_hist`, reconstruit seulement au 1er run) + marts. ⚠️ Le refresh piézo quotidien **ne construit pas** `commune_h` (il le **relit** s'il existe) — la calibration `H` est (re)construite par les refreshs **SWI mensuel** et **GASPAR hebdo**.
- CI/CD GitHub Actions (`.github/workflows/ci.yml`) : lint+types+tests, puis **déploiement SSH** sur push `main`.

> ⚠️ **mapshaper requis pour la qualité carto (A2, anti-slivers).** `make tiles` simplifie la
> **topologie** des communes via `mapshaper-xl` (arcs partagés → zéro espace blanc entre communes).
> S'il est **absent**, `build_tiles` ne casse pas mais retombe en silence sur une simplification
> tippecanoe non-topologique (les slivers peuvent réapparaître) — le log porte `mapshaper=False`.
> **Installer + épingler mapshaper sur la VM** (`npm i -g mapshaper@0.6.121`). Build national mesuré :
> GeoJSON intermédiaire ~377 Mo → mapshaper **pic RSS ~1,25 Go** (OK sur 8 Go) → `.pmtiles` ~27 Mo ;
> les GeoJSON intermédiaires sont supprimés après le build (seul le `.pmtiles` reste dans `tiles/out`).

## Bootstrap (une fois)

```bash
# Outils
curl -LsSf https://astral.sh/uv/install.sh | sh           # uv
ln -sf "$HOME/.local/bin/uv" /usr/local/bin/uv            # uv sur le PATH des shells non-login (CI)
apt-get update && apt-get install -y tippecanoe           # PMTiles (dispo en 2.49 sur Ubuntu 24.04)
apt-get install -y nodejs npm && npm i -g mapshaper@0.6.121  # simplification TOPOLOGIQUE des tuiles (A2)

# Code
git clone https://github.com/HectorBertucat/solveille.git /opt/solveille
cd /opt/solveille && uv sync --locked

# Données : seed poli (pas de re-fetch national RGA/DVF — on rsync les staging dérivés
# depuis une machine qui les a déjà), puis SWI + mart + tuiles :
#   (depuis le poste local)  rsync -az data/staging/ root@<vm>:/opt/solveille/data/staging/
cd /opt/solveille && make fetch-swi && make build-swi && make fetch-cp && make tiles
# `make tiles` génère aussi l'index de recherche communal (front/communes-index.json, via
# build_search) — gitignoré, généré sur la VM, survit aux `git reset --hard` du déploiement.
# `make fetch-cp` (codes postaux La Poste, semestriel) est manuel/occasionnel — pas de timer.

# Service API
cp deploy/systemd/solveille-api.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now solveille-api
curl -fsS localhost:8001/healthz                          # {"status":"ok"}

# Reverse proxy : ajouter l'import au Caddyfile global, sans toucher aux autres blocs
grep -q '/opt/solveille/deploy/Caddyfile' /etc/caddy/Caddyfile \
  || echo 'import /opt/solveille/deploy/Caddyfile' >> /etc/caddy/Caddyfile
systemctl reload caddy
curl -fsS -H 'Host: solveille' localhost:8083/healthz     # via Caddy

# Timer SWI mensuel
cp deploy/systemd/solveille-swi.{service,timer} /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now solveille-swi.timer
systemctl list-timers solveille-swi.timer

# Timer IPS nappes quotidien (1er run national = long, full ; ensuite incrémental)
cp deploy/systemd/solveille-piezo.{service,timer} /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now solveille-piezo.timer
systemctl start solveille-piezo.service   # 1er run manuel (full national) — suivre les logs

# Timer GASPAR hebdo (calibration H, v2) — 1er run construit catnat + commune_h + substrat
cp deploy/systemd/solveille-gaspar.{service,timer} /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now solveille-gaspar.timer
systemctl start solveille-gaspar.service  # 1er run manuel (peuple H) — suivre les logs
systemctl list-timers 'solveille-*'
```

### Cloudflare Tunnel (action manuelle, côté dashboard) — fait
Public hostname **`argile.hectorb.fr → http://localhost:8083`** ajoutée sur le tunnel **généraliste**
de la VM (celui qui sert déjà `fittrack.hectorb.fr`, connecteur *HEALTHY* = `cloudflared.service`).
⚠️ DNS + ingress doivent être sur **le même tunnel actif** (un hostname posé sur un tunnel sans
connecteur sur la VM → **Error 1033**). Service = **HTTP** (Caddy sert en clair sur `:8083`), pas HTTPS.
Site live : <https://argile.hectorb.fr>.

## CI/CD — secrets GitHub à définir

`Settings → Secrets and variables → Actions` (ou via `gh secret set`) :

| Secret | Valeur |
|---|---|
| `DEPLOY_SSH_KEY` | clé privée **dédiée** au déploiement (ed25519, sans passphrase) ; sa clé publique est dans `~/.ssh/authorized_keys` de la VM |
| `DEPLOY_HOST` | IP/host de la VM |
| `DEPLOY_USER` | `root` |
| `DEPLOY_KNOWN_HOSTS` | sortie de `ssh-keyscan -t ed25519 <host>` (épingle l'empreinte serveur) |

Sur push `main` : la CI lance lint+types+tests, puis (si vert) le job `deploy` se connecte en SSH,
fait `git reset --hard origin/main` + `uv sync --locked` + `systemctl restart solveille-api`
(`deploy/server-deploy.sh`). **Le déploiement ne rebuild pas les données** — c'est le rôle des timers.
Un changement de schéma de mart nécessite un refresh manuel après déploiement (l'API est **tolérante
au schéma** → pas de 500 entre-temps, juste les nouvelles colonnes à NULL). Pour **peupler `H` (v2)**
la 1re fois : `make fetch-gaspar` puis `systemctl start solveille-gaspar.service` (crée
`catnat_secheresse` + `commune_swi_hist` + `commune_h` + marts + tuiles).
