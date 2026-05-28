# Patrons DuckDB spatial — snippets

> Exemples indicatifs (noms de colonnes à aligner sur les schémas réels). EPSG:2154 partout au moment des jointures.

## Setup
```sql
INSTALL spatial; LOAD spatial;
INSTALL httpfs;  LOAD httpfs;
```

## Reprojection DVF (WGS84 → L93)
```sql
CREATE OR REPLACE TABLE dvf_2154 AS
SELECT *,
       ST_Transform(ST_Point(longitude, latitude), 'EPSG:4326', 'EPSG:2154') AS geom_2154
FROM read_parquet('data/staging/dvf/*.parquet')
WHERE type_local IN ('Maison');
```

## Part d'aléa RGA (moyen+fort) par commune
```sql
WITH inter AS (
  SELECT c.insee,
         SUM(ST_Area(ST_Intersection(ST_MakeValid(c.geom_2154),
                                      ST_MakeValid(r.geom_2154)))) AS aire_alea
  FROM communes c
  JOIN rga r
    ON ST_Intersects(c.geom_2154, r.geom_2154)
   AND r.niveau IN ('moyen','fort')
  GROUP BY c.insee
)
SELECT c.insee,
       COALESCE(i.aire_alea,0) / ST_Area(ST_MakeValid(c.geom_2154)) AS part_alea_moyen_fort
FROM communes c
LEFT JOIN inter i USING (insee);
```

## Anomalie SWI standardisée (par maille, par mois calendaire)
```sql
-- climatologie : moyenne/écart-type par maille et par mois (1..12) sur l'historique
WITH clim AS (
  SELECT maille, month(date_mois) AS m,
         avg(swi) AS mu, stddev_samp(swi) AS sigma
  FROM swi_history
  GROUP BY maille, month(date_mois)
)
SELECT s.maille, s.date_mois, s.swi,
       (s.swi - c.mu) / NULLIF(c.sigma,0) AS swi_anomalie   -- négatif = sec
FROM swi_current s
JOIN clim c ON c.maille = s.maille AND c.m = month(s.date_mois);
```

## IPS approché (position du niveau courant dans la distribution mensuelle)
```sql
WITH dist AS (
  SELECT code_bss, month(date_mesure) AS m, niveau_nappe_eau
  FROM piezo_history
)
SELECT cur.code_bss, cur.date_mesure,
       -- percentile empirique du niveau courant vs même mois historiquement
       (SELECT avg(CASE WHEN d.niveau_nappe_eau <= cur.niveau_nappe_eau THEN 1.0 ELSE 0.0 END)
        FROM dist d
        WHERE d.code_bss = cur.code_bss AND d.m = month(cur.date_mesure)) AS pct_rank
FROM piezo_current cur;
-- pct_rank bas = niveau bas = sec (convertir en z/IPS selon docs/metric.md)
```

## Rattachement maille SWI ↔ commune (moyenne pondérée par aire)
```sql
SELECT c.insee,
       SUM(s.swi_anomalie * ST_Area(ST_Intersection(c.geom_2154, m.geom_2154)))
       / SUM(ST_Area(ST_Intersection(c.geom_2154, m.geom_2154))) AS swi_anomalie_commune
FROM communes c
JOIN swi_mailles m ON ST_Intersects(c.geom_2154, m.geom_2154)
JOIN swi_values  s ON s.maille = m.maille
GROUP BY c.insee;
```

## Agrégat DVF par commune (agrégats only — jamais de ligne nominative)
```sql
SELECT code_commune AS insee,
       median(valeur_fonciere / NULLIF(surface_reelle_bati,0)) AS prix_median_maison_eur_m2,
       count(*) FILTER (WHERE date_mutation >= current_date - INTERVAL 12 MONTH) AS n_tx_maison_12m
FROM dvf_2154
GROUP BY code_commune;
```

## Mémoire / perf
- Lire via `read_parquet(...)`, filtrer tôt, `PRAGMA threads=4;` et `SET memory_limit='6GB';` pour rester sous l'enveloppe VM.
- Préférer des Parquet par couche à un gros état en RAM ; matérialiser les étapes intermédiaires.
