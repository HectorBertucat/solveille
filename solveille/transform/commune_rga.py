"""Intersection RGA ∩ commune → parts d'aléa par commune (entrée de l'exposition E).

Pour chaque commune : part de surface en aléa moyen / fort / (moyen+fort), classe
dominante, et `has_rga_coverage` (le zonage RGA couvre la France métropole **hors Paris**
— on distingue donc l'absence de donnée d'un vrai 0). Tout en EPSG:2154.

La jointure spatiale est bornée au même département (`code_dept`) : le zonage RGA est
dissous par (département × niveau) et une commune est incluse dans son département — cela
évite tout produit cartésien tout en restant exact.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from solveille.common import duckdb_io
from solveille.common.config import get_settings
from solveille.common.logging import get_logger

log = get_logger("solveille.transform.commune_rga")

# Étape 1 — aires d'intersection brutes par commune × niveau (table intermédiaire).
# ST_CollectionExtract(..., 3) ne garde que la composante surfacique de l'intersection
# (élimine les résidus linéaires/ponctuels des contacts tangents entre MultiPolygons).
# Bornage `code_dept` : le zonage est dissous par département et une commune ∈ son
# département → pas de produit cartésien inter-départemental, exact par construction.
_RAW_SQL = """
  WITH ca AS (
    SELECT code_insee, code_dept, ST_MakeValid(ST_GeomFromWKB(geom_wkb)) AS g
    FROM read_parquet('{commune}')
  ),
  cga AS (SELECT code_insee, code_dept, g, ST_Area(g) AS aire FROM ca),
  r AS (
    SELECT code_dept, niveau, ST_MakeValid(ST_GeomFromWKB(geom_wkb)) AS g
    FROM read_parquet('{rga}')
  ),
  inter AS (
    SELECT cga.code_insee, r.niveau,
           SUM(ST_Area(ST_CollectionExtract(ST_Intersection(cga.g, r.g), 3)))::DOUBLE AS aire_inter
    FROM cga
    JOIN r ON cga.code_dept = r.code_dept AND ST_Intersects(cga.g, r.g)
    GROUP BY cga.code_insee, r.niveau
  ),
  agg AS (
    SELECT code_insee,
           COALESCE(SUM(aire_inter) FILTER (WHERE niveau = 1), 0.0) AS a1,
           COALESCE(SUM(aire_inter) FILTER (WHERE niveau = 2), 0.0) AS a2,
           COALESCE(SUM(aire_inter) FILTER (WHERE niveau = 3), 0.0) AS a3
    FROM inter GROUP BY code_insee
  ),
  cov AS (SELECT DISTINCT code_dept FROM r)
  SELECT cga.code_insee, cga.code_dept, cga.aire,
         COALESCE(agg.a1, 0.0) AS a1, COALESCE(agg.a2, 0.0) AS a2, COALESCE(agg.a3, 0.0) AS a3,
         (cga.code_dept IN (SELECT code_dept FROM cov)) AS has_rga_coverage
  FROM cga LEFT JOIN agg USING (code_insee)
"""

# Étape 2 — parts clampées + classe dominante, dérivées de la table intermédiaire.
_FINAL_SQL = """
COPY (
  SELECT
    code_insee,
    code_dept,
    LEAST(a2 / aire, 1.0)::DOUBLE          AS part_alea_moyen,
    LEAST(a3 / aire, 1.0)::DOUBLE          AS part_alea_fort,
    LEAST((a2 + a3) / aire, 1.0)::DOUBLE   AS part_alea_moyen_fort,
    has_rga_coverage,
    CASE
      WHEN NOT has_rga_coverage      THEN NULL
      WHEN (a1 + a2 + a3) = 0        THEN 'Aucun'
      WHEN a3 >= a2 AND a3 >= a1     THEN 'Fort'
      WHEN a2 >= a1                  THEN 'Moyen'
      ELSE 'Faible'
    END                                    AS classe_dominante,
    aire                                   AS aire_commune_m2
  FROM {raw}
) TO '{out}' (FORMAT PARQUET);
"""


def build_commune_rga(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    commune_parquet: Path | None = None,
    rga_parquet: Path | None = None,
    out: Path | None = None,
) -> Path:
    """Calcule `data/staging/commune_rga.parquet` (parts d'aléa par commune).

    Garde-fou : alerte (sans bloquer) si l'aire d'aléa dépasse l'aire communale (>1 %),
    signe de niveaux RGA non disjoints ou d'un problème de rattachement départemental —
    le clamp `[0,1]` ne doit pas masquer un tel cas en silence.
    """
    s = get_settings()
    commune_parquet = commune_parquet or (s.staging_dir / "commune.parquet")
    rga_parquet = rga_parquet or (s.staging_dir / "rga.parquet")
    out = out or (s.staging_dir / "commune_rga.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)

    own = con is None
    con = con or duckdb_io.connect()
    try:
        con.execute(
            "CREATE OR REPLACE TEMP TABLE _commune_rga_raw AS "
            + _RAW_SQL.format(commune=commune_parquet, rga=rga_parquet)
        )
        overflow = duckdb_io.scalar(
            con,
            "SELECT count(*) FROM _commune_rga_raw WHERE (a1 + a2 + a3) > aire * 1.01",
        )
        if overflow:
            log.warning("commune_rga.area_overflow", n=overflow)  # niveaux non disjoints ?
        con.execute(_FINAL_SQL.format(raw="_commune_rga_raw", out=out))
        n = duckdb_io.scalar(con, f"SELECT count(*) FROM read_parquet('{out}')")
        covered = duckdb_io.scalar(
            con,
            f"SELECT count(*) FILTER (WHERE has_rga_coverage) FROM read_parquet('{out}')",
        )
    finally:
        if own:
            con.close()
    log.info(
        "staging.commune_rga", path=str(out), n_communes=n, n_couvertes=covered, overflow=overflow
    )
    return out
