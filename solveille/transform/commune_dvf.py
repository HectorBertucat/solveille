"""Agrégats DVF par commune : prix médian maison (€/m²), surface médiane, volumes.

**Légal (R112 A-3 LPF)** : ne produit que des **agrégats communaux** — aucune ligne
nominative. Pièges DVF gérés :
- une mutation (`id_mutation`) génère plusieurs lignes ; `valeur_fonciere` y est **répétée**
  → on regroupe par mutation (jamais de somme de `valeur_fonciere`) ;
- mutations multi-locaux (maison + appartement/commercial) → on ne garde que les
  **mono-bien maison** (1 maison, pas d'autre local bâti ; dépendances/terrain tolérés) ;
- valeurs aberrantes → **médiane** + bornes `[prix_min, prix_max]` €/m².

Traitement **par département** (mémoire bornée). Agrégation par `code_commune` uniquement
→ pas de reprojection ici (le rattachement spatial `n_tx_zone_exposee` est différé).
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from solveille.common import duckdb_io
from solveille.common.config import get_settings
from solveille.common.logging import get_logger

log = get_logger("solveille.transform.commune_dvf")

SOURCE_DVF = "dvf"
#: Bornes plausibles du prix maison €/m² (écarte donations 1 €, aberrants).
PRIX_MIN, PRIX_MAX = 200.0, 15000.0

_RESULT_DDL = """
CREATE OR REPLACE TEMP TABLE _dvf_result (
  code_insee VARCHAR,
  prix_median_maison_eur_m2 DOUBLE,
  surface_mediane_maison_m2 DOUBLE,
  n_tx_maison_total BIGINT,
  n_tx_maison_12m BIGINT,
  annee_min INTEGER,
  annee_max INTEGER,
  date_mutation_max DATE
)
"""

_AGG_SQL = """
INSERT INTO _dvf_result
WITH r AS (
  SELECT id_mutation,
         TRY_CAST(date_mutation AS DATE)        AS dm,
         code_commune,
         TRY_CAST(valeur_fonciere AS DOUBLE)    AS vf,
         type_local,
         TRY_CAST(surface_reelle_bati AS DOUBLE) AS surf
  FROM read_csv('{dglob}', header = true, all_varchar = true, union_by_name = true)
),
mut AS (  -- 1 ligne par mutation : on ne somme jamais valeur_fonciere (répétée)
  SELECT id_mutation,
         any_value(code_commune) AS code_commune,
         max(dm) AS dm,
         max(vf) AS vf,
         count(*) FILTER (WHERE type_local = 'Maison') AS n_maison,
         count(*) FILTER (
           WHERE type_local IN ('Appartement', 'Local industriel. commercial ou assimilé')
         ) AS n_other,
         sum(surf) FILTER (WHERE type_local = 'Maison') AS surf_maison
  FROM r GROUP BY id_mutation
),
mono AS (  -- mutations mono-bien maison, prix/m² plausible
  SELECT code_commune, dm, surf_maison, vf / surf_maison AS prix_m2
  FROM mut
  WHERE n_maison = 1 AND n_other = 0 AND vf > 0 AND surf_maison > 0
    AND vf / surf_maison BETWEEN {prix_min} AND {prix_max}
)
SELECT code_commune::VARCHAR,
       median(prix_m2)::DOUBLE,
       median(surf_maison)::DOUBLE,
       count(*)::BIGINT,
       count(*) FILTER (WHERE dm >= DATE '{max_date}' - INTERVAL 12 MONTH)::BIGINT,
       min(year(dm))::INTEGER,
       max(year(dm))::INTEGER,
       max(dm)
FROM mono WHERE code_commune IS NOT NULL GROUP BY code_commune
"""


def build_commune_dvf(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    raw_dir: Path | None = None,
    out: Path | None = None,
    prix_min: float = PRIX_MIN,
    prix_max: float = PRIX_MAX,
) -> Path:
    """Calcule `data/staging/commune_dvf.parquet` (agrégats communaux DVF, par dept)."""
    s = get_settings()
    raw_dir = raw_dir or s.source_raw_dir(SOURCE_DVF)
    out = out or (s.staging_dir / "commune_dvf.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(raw_dir.rglob("*.csv.gz"))
    if not files:
        raise FileNotFoundError("CSV DVF absents — lance d'abord `make fetch-dvf`.")
    depts = sorted({f.name.split(".")[0] for f in files})

    own = con is None
    con = con or duckdb_io.connect()
    try:
        glob_all = str(raw_dir / "*" / "*.csv.gz")
        max_date = duckdb_io.scalar(
            con,
            "SELECT max(TRY_CAST(date_mutation AS DATE)) "
            f"FROM read_csv('{glob_all}', header = true, all_varchar = true, union_by_name = true)",
        )
        max_date = str(max_date) if max_date is not None else "1900-01-01"
        con.execute(_RESULT_DDL)
        for dd in depts:
            dglob = str(raw_dir / "*" / f"{dd}.csv.gz")
            con.execute(
                _AGG_SQL.format(
                    dglob=dglob, prix_min=prix_min, prix_max=prix_max, max_date=max_date
                )
            )
        con.execute(f"COPY _dvf_result TO '{out}' (FORMAT PARQUET);")
        n = duckdb_io.scalar(con, f"SELECT count(*) FROM read_parquet('{out}')")
    finally:
        if own:
            con.close()
    log.info(
        "staging.commune_dvf", path=str(out), n_communes=n, max_date=max_date, n_depts=len(depts)
    )
    return out
