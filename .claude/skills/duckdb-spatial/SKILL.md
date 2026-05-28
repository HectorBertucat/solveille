---
name: duckdb-spatial
description: >
  Patrons DuckDB + spatial pour Solveille : reprojection EPSG:2154, jointures spatiales
  (RGA∩commune, maille SWI↔commune, piézo↔commune), parts d'aires, et fenêtres climatologiques
  (anomalie SWI standardisée, IPS). Utilise ce skill pour toute transformation géospatiale ou tout
  calcul d'anomalie/percentile, afin d'éviter les pièges de SRS et de performance mémoire.
---

# Skill : DuckDB spatial & fenêtres climatologiques

S'applique aux modules `solveille/transform/` et au SQL de `sql/`. Voir `references/patterns.md` pour les requêtes types complètes.

## Principes
- Charger les extensions une fois : `INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;`.
- **EPSG:2154 partout** au moment des jointures. DVF arrive en WGS84 → `ST_Transform(geom, 'EPSG:4326', 'EPSG:2154')`. Toujours `ST_MakeValid` avant calcul d'aires.
- **Streaming d'abord** : lire les Parquet via `read_parquet(...)`, pousser les filtres tôt, éviter `SELECT *` massif chargé en RAM (VM 8 Go). Persister chaque couche en Parquet.
- **Reproductibilité** : pas de dépendance à l'ordre des lignes ; types explicites ; clés de jointure normalisées (`code_insee` en TEXT, zéro-paddé).

## Calculs clés
- **Part d'aléa par commune** : `aire(aléa≥moyen ∩ commune) / aire(commune)`.
- **Anomalie SWI standardisée** : par **maille** et par **mois calendaire**, `(swi - moyenne_mois) / ecart_type_mois` sur la climatologie historique — surtout **ne pas** mélanger tous les mois.
- **IPS (nappes)** : par **station** et **mois**, position du niveau courant dans la distribution mensuelle historique (rang/quantile), historique idéalement ≥ 30 ans.
- **Rattachement** maille/piézo ↔ commune : intersection ou plus proche, en gérant les doublons (une commune peut toucher plusieurs mailles → moyenne pondérée par aire).

## Garde-fous
- Aucune ligne **DVF nominative** produite : n'émettre que des agrégats par commune.
- Vérifier après calcul : volumétrie, nulls, `E∈[0,1]`, couverture communes — écrire un test plutôt que vérifier à l'œil. Faire relire par le subagent `geo-duckdb-reviewer`.

Détails et snippets : `references/patterns.md`.
