"""Intersection RGA ∩ commune → parts d'aléa par commune (entrée de l'exposition E).

Pour chaque commune : part de surface en aléa moyen / fort / (moyen+fort), classe
dominante, et `has_rga_coverage` (le zonage RGA couvre la France métropole **hors Paris**
— on distingue donc l'absence de donnée d'un vrai 0). Tout en EPSG:2154.

Traitement **département par département** : le zonage est dissous par (dept × niveau) et
une commune ∈ son département → on borne l'intersection au même dept (exact, anti-cartésien)
et on plafonne le pic mémoire au plus gros département (tient le national dans 8 Go).
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from solveille.common import duckdb_io
from solveille.common.config import get_settings
from solveille.common.logging import get_logger

log = get_logger("solveille.transform.commune_rga")

# Aires d'intersection brutes (a1/a2/a3) par commune, pour UN département `{dep}`.
# ST_CollectionExtract(..., 3) ne garde que la composante surfacique de l'intersection
# (élimine les résidus linéaires/ponctuels des contacts tangents entre MultiPolygons).
_RAW_SQL_DEPT = """
  WITH ca AS (
    SELECT code_insee, code_dept, ST_MakeValid(ST_GeomFromWKB(geom_wkb)) AS g
    FROM read_parquet('{commune}') WHERE code_dept = '{dep}'
  ),
  cga AS (SELECT code_insee, code_dept, g, ST_Area(g) AS aire FROM ca),
  r AS (
    SELECT niveau, ST_MakeValid(ST_GeomFromWKB(geom_wkb)) AS g
    FROM read_parquet('{rga}') WHERE code_dept = '{dep}'
  ),
  inter AS (
    SELECT cga.code_insee, r.niveau,
           SUM(ST_Area(ST_CollectionExtract(ST_Intersection(cga.g, r.g), 3)))::DOUBLE AS aire_inter
    FROM cga JOIN r ON ST_Intersects(cga.g, r.g)
    GROUP BY cga.code_insee, r.niveau
  ),
  agg AS (
    SELECT code_insee,
           COALESCE(SUM(aire_inter) FILTER (WHERE niveau = 1), 0.0) AS a1,
           COALESCE(SUM(aire_inter) FILTER (WHERE niveau = 2), 0.0) AS a2,
           COALESCE(SUM(aire_inter) FILTER (WHERE niveau = 3), 0.0) AS a3
    FROM inter GROUP BY code_insee
  ),
  hascov AS (SELECT (count(*) > 0) AS v FROM r)
  SELECT cga.code_insee, cga.code_dept, cga.aire,
         COALESCE(agg.a1, 0.0) AS a1, COALESCE(agg.a2, 0.0) AS a2, COALESCE(agg.a3, 0.0) AS a3,
         (SELECT v FROM hascov) AS has_rga_coverage
  FROM cga LEFT JOIN agg USING (code_insee)
"""

# Parts clampées + classe dominante, dérivées de la table brute d'un département.
_FINAL_SELECT = """
  SELECT
    code_insee,
    code_dept,
    LEAST(a2 / aire, 1.0)::DOUBLE          AS part_alea_moyen,
    LEAST(a3 / aire, 1.0)::DOUBLE          AS part_alea_fort,
    LEAST((a2 + a3) / aire, 1.0)::DOUBLE   AS part_alea_moyen_fort,
    has_rga_coverage,
    CASE
      WHEN NOT has_rga_coverage   THEN NULL
      WHEN (a1 + a2 + a3) = 0     THEN 'Aucun'
      WHEN a3 >= a2 AND a3 >= a1  THEN 'Fort'
      WHEN a2 >= a1               THEN 'Moyen'
      ELSE 'Faible'
    END                                    AS classe_dominante,
    aire                                   AS aire_commune_m2
  FROM {raw}
"""

_RESULT_DDL = """
CREATE OR REPLACE TEMP TABLE _commune_rga_all (
  code_insee VARCHAR, code_dept VARCHAR,
  part_alea_moyen DOUBLE, part_alea_fort DOUBLE, part_alea_moyen_fort DOUBLE,
  has_rga_coverage BOOLEAN, classe_dominante VARCHAR, aire_commune_m2 DOUBLE
)
"""


def build_commune_rga(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    commune_parquet: Path | None = None,
    rga_parquet: Path | None = None,
    out: Path | None = None,
) -> Path:
    """Calcule `data/staging/commune_rga.parquet` (parts d'aléa par commune), par dept.

    Garde-fou : alerte (sans bloquer) si l'aire d'aléa dépasse l'aire communale (>1 %),
    signe de niveaux RGA non disjoints ou d'un problème de rattachement — le clamp `[0,1]`
    ne doit pas masquer un tel cas en silence.
    """
    s = get_settings()
    commune_parquet = commune_parquet or (s.staging_dir / "commune.parquet")
    rga_parquet = rga_parquet or (s.staging_dir / "rga.parquet")
    out = out or (s.staging_dir / "commune_rga.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)

    own = con is None
    con = con or duckdb_io.connect()
    try:
        depts = [
            r[0]
            for r in con.execute(
                f"SELECT DISTINCT code_dept FROM read_parquet('{commune_parquet}') "
                "WHERE code_dept IS NOT NULL ORDER BY 1"
            ).fetchall()
        ]
        con.execute(_RESULT_DDL)
        overflow = 0
        for dep in depts:
            con.execute(
                "CREATE OR REPLACE TEMP TABLE _cr_raw AS "
                + _RAW_SQL_DEPT.format(commune=commune_parquet, rga=rga_parquet, dep=dep)
            )
            overflow += (
                duckdb_io.scalar(
                    con, "SELECT count(*) FROM _cr_raw WHERE (a1 + a2 + a3) > aire * 1.01"
                )
                or 0
            )
            con.execute("INSERT INTO _commune_rga_all " + _FINAL_SELECT.format(raw="_cr_raw"))
        con.execute(f"COPY _commune_rga_all TO '{out}' (FORMAT PARQUET);")
        n = duckdb_io.scalar(con, f"SELECT count(*) FROM read_parquet('{out}')")
        covered = duckdb_io.scalar(
            con, f"SELECT count(*) FILTER (WHERE has_rga_coverage) FROM read_parquet('{out}')"
        )
    finally:
        if own:
            con.close()
    if overflow:
        log.warning("commune_rga.area_overflow", n=overflow)  # niveaux non disjoints ?
    log.info(
        "staging.commune_rga",
        path=str(out),
        n_communes=n,
        n_couvertes=covered,
        n_depts=len(depts),
        overflow=overflow,
    )
    return out
