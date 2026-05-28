# 00 — START HERE · Prompt de démarrage Claude Code

> Ouvre ce dossier dans **Claude Code** (Opus 4.8) et colle le bloc ci-dessous comme premier message. Mets l'effort sur **xhigh** pour la session de cadrage.

---

Tu démarres le projet **Solveille**. Avant toute action :

1. **Lis** `AGENTS.md`, puis `docs/CONCEPTION.md`, `docs/architecture.md`, `docs/data-sources.md`, `docs/metric.md`, `docs/decisions.md` et `docs/roadmap.md`. Lis aussi les skills dans `.claude/skills/`.
2. **Passe en plan mode** et rends-moi un **plan d'exécution du MVP v0** (la « carte de l'enjeu » : exposition argile × stock de maisons × valeur foncière, sans la partie dynamique). Le plan doit inclure :
   - l'arborescence du package Python (`solveille/…`) et les modules d'ingestion à créer,
   - le schéma DuckDB cible des tables `staging`/`marts` (au minimum `commune`, `commune_rga`, `epci_stock`, `commune_dvf`, `commune_pression`),
   - l'ordre des sources à ingérer pour le v0 (RGA 2026, ADMIN EXPRESS, Fideli EPCI, DVF, communes basculées 2026),
   - les contrôles qualité et tests prévus (volumétrie, nulls, cohérence SRS),
   - ce que tu comptes déléguer à des subagents read-only.
   **N'écris pas de code avant que j'aie validé ce plan.**
3. **Pousse-moi si une partie du plan n'est pas solide** ou si la conception est ambiguë — je préfère une question maintenant qu'une mauvaise hypothèse.

Contraintes non négociables (rappel d'`AGENTS.md`) : tout en **EPSG:2154** dès l'ingestion ; connecteurs **idempotents et polis** (cache + bornage, pas de matraquage des API) ; **DVF en agrégats communaux uniquement** (jamais de transaction nominative, pages en `noindex`) ; **Solveille est un indice territorial indicatif, pas un diagnostic** ; **petits commits atomiques** ; rien de destructif (`rm`, `git push`, changements d'accès) sans mon accord.

Quand le plan est validé, on construira dans l'ordre de `docs/roadmap.md` : **v0 (carte enjeu) → v1 (boussole dynamique : SWI mensuel + IPS nappes quotidien) → v2 (calibration Cat-Nat + serveur MCP)**. Pour les étapes lourdes (ingestion multi-source, calcul du mart), utilise un **effort élevé** et, si pertinent, des **subagents parallèles** puis auto-vérifie tes sorties.

Première livraison attendue après validation : `make setup` fonctionnel + l'ingestion de la **1ʳᵉ source** (RGA 2026) jusqu'à une table `commune_rga` contrôlée, avec un test.

---

### Aide-mémoire (commandes utiles)
- Démarrer : `claude` dans ce dossier ; régler l'effort via le sélecteur ou `--effort xhigh`.
- Voir la mémoire chargée : `/memory` · Gérer les agents : `/agents` · Permissions : `/permissions`.
- Lancer une exploration sans polluer le contexte : « utilise l'agent Explore pour cartographier `solveille/ingest/` ».
