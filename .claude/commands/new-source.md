---
description: Amorce l'intégration d'une nouvelle source de données (recherche → connecteur → table → test).
argument-hint: <nom-source>
---

Intègre la source **$ARGUMENTS** dans Solveille, en respectant `AGENTS.md` et le skill `opendata-connector`.

Étapes :
1. Délègue au subagent **data-source-researcher** la vérification de la source $ARGUMENTS (endpoint, schéma, SRS, cadence, volumétrie, pièges, licence) à partir de `docs/data-sources.md`.
2. Propose-moi le plan d'ingestion (paramètres polis : cache, backoff, bornage) **avant** de coder.
3. Après validation : crée `solveille/ingest/$ARGUMENTS.py` (interface `fetch()`), écris le brut dans `data/raw/$ARGUMENTS/` + `_meta.json`, puis la transformation DuckDB (reproj EPSG:2154 si géo) vers la table cible.
4. Ajoute un test de schéma sur un échantillon ; fais relire le SQL géo par **geo-duckdb-reviewer** le cas échéant.
5. Commit atomique `feat(ingest): add $ARGUMENTS connector`.

Rappel : DVF en **agrégats communaux uniquement** ; rien de destructif sans mon accord.

> Note : depuis avril 2026, les commandes personnalisées et les skills convergent dans Claude Code. Les workflows à logique métier de Solveille vivent comme **skills** (`.claude/skills/`) ; cette commande reste un simple gabarit d'amorçage.
