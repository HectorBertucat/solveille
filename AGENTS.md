# AGENTS.md — Solveille

> **Solveille** — la veille des sols argileux. Nowcast, par commune, de la **pression sécheresse–argiles (RGA)** en cours, croisée avec le **bâti exposé** et la **valeur foncière à risque**, calibrée sur l'historique **Cat-Nat sécheresse**.
>
> Ce fichier est la **source de vérité** des règles de travail pour tout agent IA (Claude Code, Codex, Cursor…). `CLAUDE.md` l'importe via `@AGENTS.md`. Garde-le court : le détail vit dans `docs/`.

## Mission
Produire un projet **public, utile et original** (portfolio data-engineering) qui combine des sources open data françaises rarement reliées pour un insight qui n'existe pas ailleurs. Voir `docs/CONCEPTION.md`.

## Stack (imposée)
- **Python 3.12**, gestion de deps avec **uv**.
- **DuckDB + extensions `spatial` et `httpfs`** = moteur analytique (fichiers Parquet sur disque, pas de serveur DB → RAM légère).
- **FastAPI** (API) + **MapLibre GL JS** (front) + **PMTiles** (tuiles vectorielles statiques, via `tippecanoe`).
- Ingestion : `httpx` + scheduler (**systemd timers**, sinon cron). Idempotent.
- Cible de déploiement : **1 VM Ubuntu, 4 vCPU / 8 Go RAM / 80 Go / 20 To out** (Hetzner CX32). Toute solution doit tenir dans cette enveloppe.

## Commandes (interface — voir `Makefile`)
- `make setup` — venv + deps + extensions DuckDB.
- `make fetch-<source>` — ingère une source dans la zone brute (`data/raw/`).
- `make build` — calcule les tables dérivées + `commune_pression` (DuckDB).
- `make tiles` — génère les PMTiles.
- `make api` — lance FastAPI en local.
- `make lint` / `make test` — ruff + mypy ; pytest.

## Conventions de code
- Type hints partout ; **ruff** (lint+format) ; **mypy** strict raisonnable.
- Connecteurs d'ingestion : 1 module par source, interface commune `fetch() -> RawDataset`, **idempotents**, **polis** (cache local, backoff, requêtes bornées par bbox/département — jamais de boucle agressive sur les API publiques).
- Données géo : tout reprojeter en **EPSG:2154 (Lambert 93)** dès l'ingestion (`ST_Transform`). DVF arrive en WGS84.
- Zones de données : `data/raw/` (brut horodaté, immuable) → `data/staging/` (Parquet nettoyé) → `data/marts/` (tables servies). Ne jamais committer `data/`.
- Pas de secret en clair ; config via `.env` (voir `.env.example`), surcharges machine dans `CLAUDE.local.md` (non versionné).

## Garde-fous (hard constraints — ne pas violer)
1. **DVF** : ne jamais exposer de transactions nominatives ni permettre l'indexation par moteurs de recherche ni la réidentification → **agrégats communaux uniquement**, `noindex` sur les pages, pas de listing brut. (Cadre légal R112 A-3 LPF.)
2. **Solveille n'est pas un diagnostic** : indicateur **territorial et indicatif**, jamais une prédiction de fissures par maison ni un conseil d'achat/assurance. Afficher ce cadrage dans l'UI.
3. **Politesse réseau** : respecter quotas et fréquences des API (Hub'eau, meteo.data.gouv, data.gouv) ; cache + bornage obligatoires. Pas de scraping de sources non prévues par la conception.
4. **Pas de destruction sans accord** : aucune suppression de données/branches, aucun `git push`, aucun changement d'accès/permissions sans validation explicite de l'humain.
5. **Sources tracées** : chaque chiffre affiché doit être rattachable à sa source + sa date de mise à jour (champ `last_updated_*`).

## Workflow attendu
1. **Plan d'abord** (mode plan / proposer avant d'exécuter), surtout pour une nouvelle source ou un changement de schéma. Pousser un plan, attendre l'aval pour les étapes lourdes.
2. **Petits pas + commits atomiques** (Conventional Commits : `feat(ingest): …`, `fix(geo): …`).
3. **Vérifier ses sorties** : après un calcul, contrôler volumétrie, valeurs nulles, cohérence géo, et écrire/exécuter un test plutôt que de vérifier à l'œil.
4. **Construire dans l'ordre de la roadmap** : MVP v0 (carte enjeu) → v1 (boussole dynamique) → v2 (calibration + MCP). Voir `docs/roadmap.md`.

## Délégation (subagents)
Les subagents de `.claude/agents/` sont **read-only** (ils n'éditent pas, ils renvoient des constats) — l'agent parent applique les écritures. Utiliser :
- `data-source-researcher` — vérifier endpoint/schéma/cadence/volume/pièges d'une source **avant** d'écrire le connecteur.
- `geo-duckdb-reviewer` — relire le SQL spatial (SRS, jointures, fenêtres percentiles).
- `metric-validator` — vérifier que le calcul `IP-RGA` respecte `docs/metric.md`.
Forker vers un subagent pour l'exploration de code afin de garder le contexte principal propre.

## Skills
`.claude/skills/` contient les workflows à logique métier :
- `opendata-connector` — patron + script pour un connecteur open-data FR poli/idempotent.
- `duckdb-spatial` — patrons de reprojection, jointures spatiales et fenêtres climatologiques.

## Travailler avec Claude (Opus 4.8)
- Mettre l'**effort `xhigh`/`extra`** sur les étapes difficiles ou les workflows asynchrones longs (ingestion multi-source, calcul du mart) ; **fast mode** pour les itérations cheap.
- Tirer parti des **dynamic workflows / subagents parallèles** pour traiter plusieurs sources en parallèle, puis **auto-vérifier** avant de rapporter.
- Si un plan n'est pas solide : **le dire et pousser** une meilleure approche plutôt que d'exécuter aveuglément.

## Références (`docs/`)
- `CONCEPTION.md` — spec maître (produit, indicateur, sources, archi, tests, déploiement).
- `data-sources.md` — playbook d'ingestion par source (endpoints, formats, SRS, pièges).
- `metric.md` — définition détaillée de l'indice `IP-RGA`.
- `architecture.md` — composants, flux de données, déploiement VM.
- `decisions.md` — journal des décisions (ADR).
- `roadmap.md` — découpage MVP v0 → v2.
