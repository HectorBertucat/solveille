# Journal des décisions (ADR)

Format léger : Décision · Contexte · Conséquences.

## ADR-001 — Maille = commune
**Décision** : la commune est la maille primaire ; l'IRIS reste un raffinement optionnel (v2+).
**Contexte** : DVF, GASPAR, le zonage RGA et les arrêtés Cat-Nat se rattachent naturellement au code INSEE commune ; bon compromis lisibilité / volumétrie / couverture nationale ; l'IRIS exigerait des données plus lourdes et moins universelles.
**Conséquences** : Fideli (maille EPCI) doit être redescendu à la commune via une clé de répartition documentée (approximation assumée).

## ADR-002 — Périmètre national
**Décision** : modèle de données et carte **nationaux** dès le départ.
**Contexte** : volumétrie modeste (SWI mensuel léger, DVF→Parquet, polygones simplifiés) → le national tient sur la VM ; un projet vitrine gagne en portée.
**Conséquences** : soigner la simplification des géométries et la pagination des API pour rester dans l'enveloppe ressources.

## ADR-003 — Cadence « nappes quotidiennes + SWI mensuel », mode poli
**Décision** : pas de temps réel à la seconde ; dynamique = SWI **mensuel** + IPS nappes **quotidien** ; ingestions **idempotentes**, **cachées** et **bornées**.
**Contexte** : l'utilisateur ne veut pas surcharger le serveur ni les API publiques ; le phénomène RGA évolue lentement.
**Conséquences** : afficher `last_updated_*` ; la « fraîcheur » est honnête (mensuelle/quotidienne), pas instantanée.

## ADR-004 — DuckDB sans serveur + Parquet
**Décision** : DuckDB (`spatial`,`httpfs`) sur fichiers Parquet, pas de SGBD serveur.
**Contexte** : 8 Go de RAM ; jointures spatiales et fenêtres percentiles bien supportées par DuckDB ; déjà maîtrisé.
**Conséquences** : privilégier les requêtes sur Parquet (streaming) plutôt que le chargement mémoire complet.

## ADR-005 — PMTiles statiques pour la carte
**Décision** : choroplèthe servie via PMTiles (tippecanoe), fichier statique.
**Contexte** : cheap, rapide, range requests, 20 To d'egress largement suffisants ; évite un tile-server.
**Conséquences** : régénérer les tuiles quand `commune_pression` change.

## ADR-006 — DVF en agrégats uniquement
**Décision** : n'exposer que des agrégats communaux ; pages `noindex` ; pas de réidentification.
**Contexte** : contrainte légale DVF (R112 A-3 LPF : pas d'indexation moteurs, pas de réidentification).
**Conséquences** : pas de listing de transactions ; le prix médian commune suffit pour l'enjeu.

## ADR-007 — Indice indicatif, pas un diagnostic
**Décision** : positionner Solveille comme **indice territorial indicatif**.
**Contexte** : le RGA à l'échelle d'un bâtiment dépend de facteurs locaux non modélisés ; éviter tout faux signal de sécurité structurelle ou conseil.
**Conséquences** : cadrage explicite dans l'UI ; pas de score par maison ; calibration `H` présentée comme indicative.

## ADR-008 — AGENTS.md source de vérité, CLAUDE.md pointeur
**Décision** : règles d'agent dans `AGENTS.md` (tool-agnostic) ; `CLAUDE.md` importe via `@AGENTS.md` + surcharges Claude Code.
**Contexte** : portabilité entre assistants (Codex/Cursor/Copilot lisent AGENTS.md) ; Claude Code lit nativement CLAUDE.md.
**Conséquences** : éviter la divergence des deux fichiers ; technicité fine déléguée aux skills (chargés à la demande).

## ADR-009 — Subagents read-only
**Décision** : les subagents (`.claude/agents/`) n'éditent pas ; ils renvoient des constats ; les écritures restent au parent.
**Contexte** : bonne pratique Claude Code (les outils d'édition d'un subagent contournent les prompts d'approbation).
**Conséquences** : recherche/relecture/validation déléguées ; application centralisée et contrôlée.

## ADR-010 — Nom de travail « Solveille »
**Décision** : nom **Solveille** (mot-valise *sol* + *veille*).
**Contexte** : « RGA » est opaque ; il fallait un nom évocateur (surveillance des sols), libre (Argiléo déjà pris par un enduit). Alternatives : Terravigie, Glaise, Boussole.
**Conséquences** : renommable en un remplacement global (`solveille` → autre) si besoin.
