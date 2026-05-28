# CLAUDE.md

> **Toutes les règles de travail vivent dans `AGENTS.md`** (source de vérité, tool-agnostic).
> Claude Code lit ce fichier à chaque session ; l'import ci-dessous charge AGENTS.md, puis viennent les points spécifiques à Claude Code.

@AGENTS.md

## Spécifique Claude Code
- **Plan mode** pour toute nouvelle source ou tout changement de schéma : proposer le plan, attendre l'aval avant les étapes lourdes.
- **Effort** : `xhigh` (ou `extra`) sur l'ingestion multi-source, le calcul du mart `commune_pression`, et le calibrage Cat-Nat ; `fast`/effort bas pour les itérations rapides et la doc.
- **Subagents** (`.claude/agents/`, read-only) : déléguer la recherche de source (`data-source-researcher`), la relecture du SQL spatial (`geo-duckdb-reviewer`) et la validation de l'indice (`metric-validator`) ; les **écritures restent au parent**.
- **Skills** (`.claude/skills/`) : invoquer `opendata-connector` pour tout nouveau connecteur, `duckdb-spatial` pour les jointures géo / fenêtres percentiles.
- **Dynamic workflows / subagents parallèles** : exploiter pour traiter plusieurs sources en parallèle puis auto-vérifier les sorties.
- **Permissions** : voir `.claude/settings.json`. Écritures dans le repo OK ; `git push`, `rm`, `docker` demandent confirmation ; `rm -rf` et `curl | bash` interdits.
- **Sous-dossiers** : on pourra ajouter des `CLAUDE.md` ciblés (ex. `front/CLAUDE.md`) chargés à la demande, plutôt que de gonfler ce fichier.

## Local (non versionné)
- `CLAUDE.local.md` et `.env` contiennent les réglages machine (chemins, éventuels jetons d'API tierces). **Ne pas committer.**
