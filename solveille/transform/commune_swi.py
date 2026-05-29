"""Rattachement maille SWI ↔ commune, puis z_SWI communal mensuel (tension hydrique `T`).

Deux étapes (cf. skill `duckdb-spatial`, ADR-016) :
1. **`commune_maille_poids`** (STATIQUE) : la grille SWI ne fournit que des centroïdes → on
   reconstruit le **carré 8 km** de chaque maille (`x93±4000`, `y93±4000`, en EPSG:2154) et on
   pèse chaque (commune, maille) par l'**aire d'intersection** carré∩commune. La grille et les
   communes ne bougent pas → calcul fait une fois. Traitement **département par département**
   (anti-OOM, comme `commune_rga.py`).
2. **`commune_swi`** (TEMPOREL, léger) : `z_SWI` communal = moyenne des `z_SWI` des mailles
   **pondérée par l'aire d'intersection**, par mois. Couverture 100 % (grille 8 km totale).

Tout en EPSG:2154 (communes en WKB L93, mailles déjà en L93 → pas de reprojection).
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from solveille.common import duckdb_io
from solveille.common.config import get_settings
from solveille.common.logging import get_logger

log = get_logger("solveille.transform.commune_swi")

#: Demi-côté de la maille SWI (maille = 8 km → ±4000 m autour du centroïde).
MAILLE_HALF_M = 4000.0

# Poids (commune, maille) pour UN département `{dep}` : carré 8 km ∩ commune.
# Candidats mailles bornés par la bbox du département (+ marge maille) → anti-cartésien.
# ST_CollectionExtract(...,3) : ne garde que la composante surfacique (résidus tangents).
_POIDS_SQL_DEPT = """
  WITH ca AS (
    SELECT code_insee, ST_MakeValid(ST_GeomFromWKB(geom_wkb)) AS g
    FROM read_parquet('{commune}') WHERE code_dept = '{dep}'
  ),
  bb AS (
    SELECT min(ST_XMin(g)) AS xmin, min(ST_YMin(g)) AS ymin,
           max(ST_XMax(g)) AS xmax, max(ST_YMax(g)) AS ymax
    FROM ca
  ),
  mailles AS (
    SELECT num_maille,
           ST_MakeEnvelope(x93 - {h}, y93 - {h}, x93 + {h}, y93 + {h}) AS sq
    FROM read_parquet('{grille}'), bb
    WHERE x93 BETWEEN bb.xmin - {h} AND bb.xmax + {h}
      AND y93 BETWEEN bb.ymin - {h} AND bb.ymax + {h}
  )
  SELECT ca.code_insee, mailles.num_maille,
         ST_Area(ST_CollectionExtract(ST_Intersection(ca.g, mailles.sq), 3)) AS poids_aire
  FROM ca JOIN mailles ON ST_Intersects(ca.g, mailles.sq)
"""

_POIDS_DDL = """
CREATE OR REPLACE TEMP TABLE _commune_maille_poids (
  code_insee VARCHAR, num_maille INTEGER, poids_aire DOUBLE
)
"""

# Repli : communes qu'aucun carré-maille ne couvre (îles hors grille SAFRAN, ex. Ouessant,
# Île-de-Sein) → rattachées à la **maille la plus proche** du centroïde (poids = aire commune,
# soit le comportement « commune entière dans une maille »). Garantit la couverture 100 %.
_POIDS_FALLBACK_SQL = """
  WITH miss AS (
    SELECT code_insee, ST_MakeValid(ST_GeomFromWKB(geom_wkb)) AS g
    FROM read_parquet('{commune}')
    WHERE code_insee NOT IN (SELECT DISTINCT code_insee FROM _commune_maille_poids)
  ),
  ranked AS (
    SELECT m.code_insee, gr.num_maille, ST_Area(m.g) AS poids_aire,
           row_number() OVER (
             PARTITION BY m.code_insee
             ORDER BY ST_Distance(ST_Centroid(m.g), ST_Point(gr.x93, gr.y93))
           ) AS rn
    FROM miss m, read_parquet('{grille}') gr
  )
  SELECT code_insee, num_maille, poids_aire FROM ranked WHERE rn = 1
"""

# z_SWI communal = Σ(z·poids)/Σ(poids) sur les mailles à z non nul, par (commune, mois).
_COMMUNE_SWI_SQL = """
COPY (
  WITH contrib AS (
    SELECT p.code_insee, a.date_mois, a.z_swi, p.poids_aire
    FROM read_parquet('{poids}') p
    JOIN read_parquet('{anomalie}') a USING (num_maille)
    WHERE a.z_swi IS NOT NULL AND p.poids_aire > 0
  )
  SELECT code_insee,
         date_mois,
         sum(z_swi * poids_aire) / sum(poids_aire) AS z_swi,
         count(*)                                  AS n_mailles
  FROM contrib
  GROUP BY code_insee, date_mois
) TO '{out}' (FORMAT PARQUET);
"""


def build_commune_maille_poids(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    commune_parquet: Path | None = None,
    grille_parquet: Path | None = None,
    out: Path | None = None,
) -> Path:
    """Calcule `data/staging/commune_maille_poids.parquet` (aire carré-maille ∩ commune), par dept.

    Garde-fou : alerte si des communes (du périmètre) ne touchent **aucune** maille — la grille
    8 km couvre toute la métropole, donc une couverture < 100 % signale un problème de SRS/grille.
    """
    s = get_settings()
    commune_parquet = commune_parquet or (s.staging_dir / "commune.parquet")
    grille_parquet = grille_parquet or (s.staging_dir / "swi_grille.parquet")
    out = out or (s.staging_dir / "commune_maille_poids.parquet")
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
        con.execute(_POIDS_DDL)
        for dep in depts:
            con.execute(
                "INSERT INTO _commune_maille_poids "
                + _POIDS_SQL_DEPT.format(
                    commune=commune_parquet, grille=grille_parquet, dep=dep, h=MAILLE_HALF_M
                )
                + " WHERE poids_aire > 0"
            )
        # Repli plus-proche-maille pour les communes non couvertes (îles hors grille).
        before = duckdb_io.scalar(
            con, "SELECT count(DISTINCT code_insee) FROM _commune_maille_poids"
        )
        con.execute(
            "INSERT INTO _commune_maille_poids "
            + _POIDS_FALLBACK_SQL.format(commune=commune_parquet, grille=grille_parquet)
        )
        after = duckdb_io.scalar(
            con, "SELECT count(DISTINCT code_insee) FROM _commune_maille_poids"
        )
        n_fallback = (after or 0) - (before or 0)
        con.execute(f"COPY _commune_maille_poids TO '{out}' (FORMAT PARQUET);")
        n_pairs = duckdb_io.scalar(con, f"SELECT count(*) FROM read_parquet('{out}')")
        n_communes = after
        n_total = duckdb_io.scalar(con, f"SELECT count(*) FROM read_parquet('{commune_parquet}')")
    finally:
        if own:
            con.close()
    sans_maille = (n_total or 0) - (n_communes or 0)
    if sans_maille:
        log.warning("commune_maille_poids.communes_sans_maille", n=sans_maille)  # SRS/grille ?
    log.info(
        "staging.commune_maille_poids",
        path=str(out),
        n_paires=n_pairs,
        n_communes=n_communes,
        n_depts=len(depts),
        n_repli_plus_proche=n_fallback,
        communes_sans_maille=sans_maille,
    )
    return out


def build_commune_swi(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    poids_parquet: Path | None = None,
    anomalie_parquet: Path | None = None,
    out: Path | None = None,
) -> Path:
    """Calcule `data/staging/commune_swi.parquet` : z_SWI communal mensuel (moyenne des
    mailles pondérée par l'aire d'intersection)."""
    s = get_settings()
    poids_parquet = poids_parquet or (s.staging_dir / "commune_maille_poids.parquet")
    anomalie_parquet = anomalie_parquet or (s.staging_dir / "swi_anomalie.parquet")
    out = out or (s.staging_dir / "commune_swi.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)

    own = con is None
    con = con or duckdb_io.connect()
    try:
        con.execute(
            _COMMUNE_SWI_SQL.format(poids=poids_parquet, anomalie=anomalie_parquet, out=out)
        )
        stats = con.execute(
            f"""SELECT count(*), count(DISTINCT code_insee),
                       count(DISTINCT date_mois),
                       round(avg(z_swi), 4), round(stddev_samp(z_swi), 4)
                FROM read_parquet('{out}')"""
        ).fetchone()
        n, n_comm, n_mois, zmean, zstd = stats if stats else (0, 0, 0, None, None)
    finally:
        if own:
            con.close()
    log.info(
        "staging.commune_swi",
        path=str(out),
        n_lignes=n,
        n_communes=n_comm,
        n_mois=n_mois,
        z_moyen=zmean,
        z_ecart_type=zstd,
    )
    return out
