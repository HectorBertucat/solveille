# Architecture

## Principe
**Fichiers Parquet + DuckDB** comme moteur analytique (pas de serveur de base de données) pour tenir dans **8 Go de RAM**. Ingestion planifiée et idempotente. Sortie servie en **statique (PMTiles)** + **API FastAPI**. MCP en v2.

## Flux de données
```
                ┌─────────────── scheduler (systemd timers / cron) ───────────────┐
                │  quotidien: Hub'eau · mensuel: SWI · hebdo: GASPAR · semestriel: DVF · annuel: IGN/Fideli │
                └───────────────────────────────┬─────────────────────────────────┘
                                                 ▼
   Sources (API + fichiers)  ──httpx──►  data/raw/<source>/ (+ _meta.json)   [brut, immuable]
                                                 │  (skill: opendata-connector)
                                                 ▼
                           DuckDB + spatial + httpfs
                           ├─ reprojection → EPSG:2154 (ST_Transform)
                           ├─ nettoyage/typage           → data/staging/*.parquet
                           ├─ jointures spatiales (RGA∩commune, maille SWI↔commune, piézo↔commune)
                           ├─ fenêtres climatologiques (anomalie SWI, IPS)   (skill: duckdb-spatial)
                           └─ calcul IP-RGA               → data/marts/commune_pression.parquet
                                                 │
                  ┌──────────────────────────────┼───────────────────────────┐
                  ▼                               ▼                           ▼
        tippecanoe → PMTiles            FastAPI (uvicorn)              (v2) serveur MCP
        (choroplèthe commune,           /communes/{insee}             tools: pression par
         fichier statique)              /pression /lookup /meta        commune / adresse
                  │                               │
                  └────────────► MapLibre GL JS (front statique) ◄─────┘
```

## Arborescence du dépôt (cible — à créer par Claude Code)
```
solveille/
├── solveille/                 # package Python
│   ├── ingest/                # 1 module par source (interface fetch() -> RawDataset)
│   ├── transform/             # SQL/py DuckDB : staging, jointures, climatologie
│   ├── metric/                # calcul IP-RGA (voir docs/metric.md)
│   ├── api/                   # FastAPI
│   ├── mcp/                   # (v2) serveur MCP
│   └── common/                # config (.env), io, géo, logging, manifestes
├── sql/                       # requêtes DuckDB versionnées
├── tiles/                     # config tippecanoe / sortie PMTiles
├── front/                     # MapLibre (statique)
├── tests/                     # unit + contrats de données + métier
├── data/                      # raw/ staging/ marts/  (gitignored)
├── docs/  .claude/  Makefile  pyproject.toml  .env.example
```

## Composants
- **Ingestion** : `httpx` (timeouts, retries+backoff), pagination polie, cache local (ETag/last-modified si dispo), bornage par bbox/département. Chaque run écrit `_meta.json`. Voir le skill `opendata-connector`.
- **Transform/compute** : DuckDB (`spatial`, `httpfs`). Tout en **EPSG:2154**. Jointures spatiales et fenêtres percentiles → voir le skill `duckdb-spatial`. Persistance en Parquet par couche.
- **Tiles** : `tippecanoe` génère un PMTiles communal (join des attributs `commune_pression`), servi en statique (range requests).
- **API** : FastAPI/uvicorn, lecture directe des Parquet/DuckDB. OpenAPI auto. `noindex`.
- **Front** : MapLibre GL JS + PMTiles, légende 5 niveaux, curseur de date, fiche commune, bandeau de cadrage.
- **MCP (v2)** : expose la pression par commune/adresse comme tools (réutilise `/lookup`).

## Déploiement (VM Ubuntu 4 vCPU / 8 Go / 80 Go / 20 To)
- Reverse proxy **Caddy** (TLS auto) → uvicorn + statique (front + PMTiles).
- **systemd timers** pour les ingestions (cf. cadences). Jobs idempotents, verrou simple anti-chevauchement.
- Budgets : stockage < 80 Go (DVF→Parquet, SWI/piézo agrégés, PMTiles compact) ; RAM 8 Go OK avec DuckDB en streaming (éviter de tout charger en mémoire — préférer requêtes sur Parquet) ; egress trivial.
- Sauvegarde légère : `data/marts/` + manifestes (le brut est re-téléchargeable).

## Choix structurants (voir `decisions.md`)
Maille **commune** ; périmètre **national** ; cadence **nappes quotidiennes + SWI mensuel** (mode poli) ; **DuckDB sans serveur** ; **PMTiles statiques** ; subagents **read-only**.
