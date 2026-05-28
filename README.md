# Solveille

> **La veille des sols argileux.** Nowcast, par commune et à l'échelle nationale, de la **pression sécheresse–argiles (RGA)** en cours, croisée avec le **bâti exposé** et la **valeur foncière à risque**, et calibrée sur l'historique des **catastrophes naturelles sécheresse**.

Projet open data / portfolio data-engineering. **Solveille est un indice territorial *indicatif*, pas un diagnostic de bâtiment ni un conseil d'achat/assurance.**

## Démarrer (avec Claude Code)
1. Ouvre ce dossier dans **Claude Code** (Opus 4.8).
2. Lis et colle le prompt de **[`00-START-HERE.md`](00-START-HERE.md)** comme premier message (effort `xhigh`).
3. Claude lira `AGENTS.md` + `docs/`, te proposera un **plan** du MVP v0, puis construira dans l'ordre de [`docs/roadmap.md`](docs/roadmap.md).

## Comprendre le projet
- **Spec maître** : [`docs/CONCEPTION.md`](docs/CONCEPTION.md)
- **Indicateur IP-RGA** : [`docs/metric.md`](docs/metric.md)
- **Sources & ingestion** : [`docs/data-sources.md`](docs/data-sources.md)
- **Architecture & déploiement** : [`docs/architecture.md`](docs/architecture.md)
- **Décisions (ADR)** : [`docs/decisions.md`](docs/decisions.md)
- **Roadmap** : [`docs/roadmap.md`](docs/roadmap.md)

## Cadre agentique (bonnes pratiques)
- **`AGENTS.md`** = source de vérité (tool-agnostic). **`CLAUDE.md`** = pointeur (`@AGENTS.md` + spécificités Claude Code).
- **Subagents** read-only dans `.claude/agents/` : `data-source-researcher`, `geo-duckdb-reviewer`, `metric-validator` (ils renvoient des constats ; les écritures restent au parent).
- **Skills** dans `.claude/skills/` : `opendata-connector` (connecteurs polis/idempotents) et `duckdb-spatial` (jointures géo + anomalies). Les workflows métier vivent comme skills ; `.claude/commands/new-source.md` est un gabarit d'amorçage.
- **Permissions** : `.claude/settings.json` (écritures repo OK ; `git push`/`rm`/`docker` à confirmer ; `rm -rf` interdit ; `.env`/`CLAUDE.local.md` non lisibles).

## Stack
Python 3.12 (uv) · DuckDB + spatial/httpfs · FastAPI · MapLibre GL JS · PMTiles (tippecanoe) · httpx · systemd timers. Cible : 1 VM Ubuntu 4 vCPU / 8 Go / 80 Go / 20 To.

## Données & licences
Sources : Géorisques/BRGM (RGA), Météo-France (SWI), Hub'eau/ADES (nappes), DGPR (GASPAR), DGFiP (DVF), IGN (ADMIN EXPRESS), INSEE. Licence Ouverte 2.0 / ODbL selon source — **afficher les attributions**. **DVF : agrégats communaux uniquement, pages `noindex`, pas de réidentification.**

## Renommer le projet
« Solveille » est un nom de travail : un remplacement global `solveille` → `<nouveau>` suffit (package, repo, README).
