"""IPS piézométrique recalculé + rattachement aux communes (raffinement local de `T`, ADR-018).

1. **`piezo_ips`** : par `(code_bss, mois servi)`, deux standardisations du niveau NGF contre la
   **climatologie du même mois calendaire** (tout l'historique de la station, ≥ 15 ans) :
   - `z_ips` plain `(x−μ)/σ` → pilote `T` (même méthode que `z_SWI`, cohérence inter-signaux) ;
   - `ips_nqt = Φ⁻¹(rang_Weibull)` (NQT, méthode BRGM) → `ips_classe` (7 classes exactes).
   `Φ⁻¹` = macro DuckDB `probit` (approx. Acklam, ~3e-9 vs `metric.probit` ⇒ parité testée).
2. **`commune_ips`** : rattachement **point-dans-commune** (`ST_Contains`, par dept anti-OOM ;
   repli `code_commune_insee` si non contenu), agrégat **pondéré par la confiance** des stations.

NGF haut = humide ⇒ `z_ips`/`ips_nqt` négatifs = sec (cohérent avec `dry = sigma(-GAIN·z)`).
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from solveille.common import duckdb_io
from solveille.common.config import SWI_SERVED_FROM, get_settings
from solveille.common.logging import get_logger
from solveille.metric.ip_rga import (
    IPS_CLASS_SEUILS,
    IPS_CONF_FLOOR,
    IPS_FULL_YEARS,
    IPS_MIN_YEARS,
)
from solveille.transform.piezo import PIEZO_MEMORY_LIMIT

log = get_logger("solveille.transform.piezo_ips")

#: Macro DuckDB `probit(p)` = quantile normal standard Φ⁻¹ (approximation rationnelle d'Acklam,
#: ~3e-9 vs `metric.probit`). Sans dépendance (les UDF Python DuckDB exigent numpy). Clampé
#: loin de 0/1 (anti-infini), médiane p=0.5 → 0.
_PROBIT_MACRO_SQL = """
CREATE OR REPLACE MACRO probit(p) AS (
  WITH c AS (SELECT least(greatest(p::DOUBLE, 1e-9), 1.0 - 1e-9) AS pp)
  SELECT CASE
    WHEN pp < 0.02425 THEN
      (((((-7.784894002430293e-03*ql + -3.223964580411365e-01)*ql + -2.400758277161838e+00)*ql
          + -2.549732539343734e+00)*ql + 4.374664141464968e+00)*ql + 2.938163982698783e+00)
      / ((((7.784695709041462e-03*ql + 3.224671290700398e-01)*ql + 2.445134137142996e+00)*ql
          + 3.754408661907416e+00)*ql + 1.0)
    WHEN pp > 0.97575 THEN
      -(((((-7.784894002430293e-03*qu + -3.223964580411365e-01)*qu + -2.400758277161838e+00)*qu
           + -2.549732539343734e+00)*qu + 4.374664141464968e+00)*qu + 2.938163982698783e+00)
      / ((((7.784695709041462e-03*qu + 3.224671290700398e-01)*qu + 2.445134137142996e+00)*qu
          + 3.754408661907416e+00)*qu + 1.0)
    ELSE
      (((((-3.969683028665376e+01*rr + 2.209460984245205e+02)*rr + -2.759285104469687e+02)*rr
          + 1.383577518672690e+02)*rr + -3.066479806614716e+01)*rr + 2.506628277459239e+00)*qm
      / (((((-5.447609879822406e+01*rr + 1.615858368580409e+02)*rr + -1.556989798598866e+02)*rr
          + 6.680131188771972e+01)*rr + -1.328068155288572e+01)*rr + 1.0)
  END
  FROM c,
       LATERAL (SELECT sqrt(-2.0*ln(pp)) AS ql, sqrt(-2.0*ln(1.0 - pp)) AS qu, (pp - 0.5) AS qm) v,
       LATERAL (SELECT qm*qm AS rr) w
);
"""

#: Confiance d'une station (réplique SQL de `metric.confiance_ips`, f_nappe=f_repr=1 en M1).
_CONF_EXPR = (
    f"CASE WHEN span_annees < {IPS_MIN_YEARS} THEN 0.0 "
    f"WHEN span_annees >= {IPS_FULL_YEARS} THEN 1.0 "
    f"ELSE {IPS_CONF_FLOOR} + {1.0 - IPS_CONF_FLOOR} * (span_annees - {IPS_MIN_YEARS}) "
    f"/ {float(IPS_FULL_YEARS - IPS_MIN_YEARS)} END"
)


def _classe_case(col: str) -> str:
    """CASE SQL binnant `col` (IPS NQT) sur les 7 classes BRGM (réplique `metric.ips_classe`)."""
    whens = " ".join(f"WHEN {col} <= {s} THEN {i}" for i, s in enumerate(IPS_CLASS_SEUILS))
    return f"CASE WHEN {col} IS NULL THEN NULL {whens} ELSE {len(IPS_CLASS_SEUILS)} END"


# Par (code_bss, mois calendaire) : μ/σ, rang de Weibull. z_ips plain + ips_nqt via probit ;
# gating n_years ≥ MIN. Climatologie sur tout l'historique ; sortie limitée à la fenêtre servie.
_IPS_SQL = """
COPY (
  WITH ranked AS (
    SELECT code_bss, date_mois, month(date_mois) AS mois_cal, ngf,
           avg(ngf)             OVER w AS mu,
           stddev_samp(ngf)     OVER w AS sigma,
           rank() OVER (PARTITION BY code_bss, month(date_mois) ORDER BY ngf) AS r,
           count(*)             OVER w AS n_years
    FROM read_parquet('{mensuel}')
    WHERE ngf IS NOT NULL
    WINDOW w AS (PARTITION BY code_bss, month(date_mois))
  ),
  scored AS (
    SELECT code_bss, date_mois, n_years,
           CASE WHEN n_years < {min_years} OR sigma IS NULL OR sigma < 1e-9 THEN NULL
                ELSE (ngf - mu) / sigma END AS z_ips,
           CASE WHEN n_years < {min_years} THEN NULL
                ELSE probit(r::DOUBLE / (n_years + 1.0)) END AS ips_nqt
    FROM ranked
  )
  SELECT code_bss, date_mois, n_years, z_ips, ips_nqt, {classe} AS ips_classe
  FROM scored
  WHERE date_mois >= DATE '{served_from}'
) TO '{out}' (FORMAT PARQUET);
"""


def build_piezo_ips(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    mensuel_parquet: Path | None = None,
    out: Path | None = None,
    served_from: str = SWI_SERVED_FROM,
) -> Path:
    """Construit `data/staging/piezo_ips.parquet` (z_ips/ips_nqt/classe par station-mois)."""
    s = get_settings()
    mensuel_parquet = mensuel_parquet or (s.staging_dir / "piezo_mensuel.parquet")
    out = out or (s.staging_dir / "piezo_ips.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)

    own = con is None
    con = con or duckdb_io.connect(memory_limit=PIEZO_MEMORY_LIMIT)
    try:
        con.execute(_PROBIT_MACRO_SQL)
        con.execute(
            _IPS_SQL.format(
                mensuel=mensuel_parquet,
                min_years=IPS_MIN_YEARS,
                classe=_classe_case("ips_nqt"),
                served_from=served_from,
                out=out,
            )
        )
        stats = con.execute(
            f"""SELECT count(*), count(DISTINCT code_bss),
                       count(*) FILTER (WHERE z_ips IS NOT NULL),
                       round(avg(z_ips), 4), round(stddev_samp(z_ips), 4)
                FROM read_parquet('{out}')"""
        ).fetchone()
        n, n_bss, n_z, zmean, zstd = stats if stats else (0, 0, 0, None, None)
    finally:
        if own:
            con.close()
    log.info(
        "staging.piezo_ips",
        path=str(out),
        n_lignes=n,
        n_stations=n_bss,
        n_z_non_null=n_z,
        z_moyen=zmean,
        z_ecart_type=zstd,
    )
    return out


_STATION_COMMUNE_DDL = """
CREATE OR REPLACE TEMP TABLE _station_commune (
  code_bss VARCHAR, code_insee VARCHAR, confiance DOUBLE, methode VARCHAR
)
"""

# Stations avec confiance + point 2154 (réutilisé pour le rattachement spatial et le repli INSEE).
_PZ_DDL = """
CREATE OR REPLACE TEMP TABLE _pz AS
SELECT code_bss, code_commune_insee, code_departement,
       ST_GeomFromWKB(geom_wkb) AS pt, {conf} AS confiance
FROM read_parquet('{stations}')
"""

# Rattachement spatial point-dans-commune pour UN département (anti-OOM).
_SPATIAL_DEPT_SQL = """
INSERT INTO _station_commune
SELECT p.code_bss, c.code_insee, p.confiance, 'spatial'
FROM _pz p
JOIN (
  SELECT code_insee, ST_MakeValid(ST_GeomFromWKB(geom_wkb)) AS g
  FROM read_parquet('{commune}') WHERE code_dept = '{dep}'
) c ON ST_Contains(c.g, p.pt)
WHERE p.code_departement = '{dep}'
"""

#: Rayon de représentativité (m) : une commune SANS station hôte hérite de la station la plus
#: proche < R, avec une confiance décroissant linéairement (`f_repr = 1 − d/R`). Conservateur
#: (l'effet se concentre près des stations) ; tunable. BDLISA libre/captive = raffinement futur.
REPR_RADIUS_M = 10000.0

# Représentativité : communes du dept SANS station hôte ← station la plus proche < REPR_RADIUS_M
# du centroïde, confiance = f_hist · f_repr (`f_repr = 1 − d/R`). Anti-OOM par dept (approx :
# stations du même dept ; une station inter-dept plus proche en bordure est ignorée).
_REPR_DEPT_SQL = """
INSERT INTO _station_commune
WITH miss AS (
  SELECT code_insee, ST_Centroid(ST_MakeValid(ST_GeomFromWKB(geom_wkb))) AS ctr
  FROM read_parquet('{commune}') WHERE code_dept = '{dep}'
    AND code_insee NOT IN (SELECT code_insee FROM _station_commune)
),
cand AS (
  SELECT m.code_insee, p.code_bss, p.confiance, ST_Distance(m.ctr, p.pt) AS d,
         row_number() OVER (PARTITION BY m.code_insee
                            ORDER BY ST_Distance(m.ctr, p.pt), p.code_bss) AS rn
  FROM miss m JOIN _pz p ON p.code_departement = '{dep}'
  WHERE ST_Distance(m.ctr, p.pt) <= {r}
)
SELECT code_bss, code_insee, confiance * (1.0 - d / {r}), 'repr'
FROM cand WHERE rn = 1
"""

# Repli : stations non contenues spatialement → rattachées via leur code_commune_insee (ADES).
_FALLBACK_SQL = """
INSERT INTO _station_commune
SELECT p.code_bss, c.code_insee, p.confiance, 'insee'
FROM _pz p
JOIN read_parquet('{commune}') c ON c.code_insee = p.code_commune_insee
WHERE p.code_commune_insee IS NOT NULL
  AND p.code_bss NOT IN (SELECT code_bss FROM _station_commune)
"""

# Agrégat communal : moyenne pondérée par confiance des stations rattachées (z_ips/ips_nqt sur
# les mois à z non nul) ; confiance commune = meilleure station. `confiance > 0` exclut les
# stations à span < 15 ans (anti `0/0` si une telle station était la seule). La classe communale
# n'est PAS produite ici (le mart bin le nqt communal en M2) — `commune_ips` n'expose que
# z_ips/ips_nqt/confiance/n_stations.
_COMMUNE_IPS_SQL = """
COPY (
  SELECT sc.code_insee AS insee, pi.date_mois,
         sum(pi.z_ips   * sc.confiance) / sum(sc.confiance) AS z_ips,
         sum(pi.ips_nqt * sc.confiance) / sum(sc.confiance) AS ips_nqt,
         max(sc.confiance)                                  AS confiance,
         count(DISTINCT sc.code_bss)                        AS n_stations
  FROM _station_commune sc
  JOIN read_parquet('{piezo_ips}') pi USING (code_bss)
  WHERE pi.z_ips IS NOT NULL AND sc.confiance > 0
  GROUP BY sc.code_insee, pi.date_mois
) TO '{out}' (FORMAT PARQUET);
"""

# Dédoublonnage spatial : un point sur une frontière (intra ou inter-dept) peut être contenu par
# 2 communes → on garde un seul rattachement déterministe (min code_insee) par station, sinon la
# station compterait dans 2 communes et biaiserait l'agrégat.
_DEDUP_SPATIAL_SQL = """
CREATE OR REPLACE TEMP TABLE _station_commune AS
SELECT code_bss, min(code_insee) AS code_insee, max(confiance) AS confiance,
       any_value(methode) AS methode
FROM _station_commune
GROUP BY code_bss
"""


def build_commune_ips(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    stations_parquet: Path | None = None,
    piezo_ips_parquet: Path | None = None,
    commune_parquet: Path | None = None,
    out: Path | None = None,
    radius_m: float = REPR_RADIUS_M,
) -> Path:
    """Construit `data/staging/commune_ips.parquet` (z_ips/ips_nqt/confiance communaux mensuels).

    Rattachement **hôte** (point-dans-commune par dept, repli `code_commune_insee`) puis
    **représentativité** (communes sans hôte ← station la plus proche < `radius_m`, confiance
    décroissante). Anti-jointure loggée (stations rattachées à aucune commune du périmètre).
    """
    s = get_settings()
    stations_parquet = stations_parquet or (s.staging_dir / "piezo_stations.parquet")
    piezo_ips_parquet = piezo_ips_parquet or (s.staging_dir / "piezo_ips.parquet")
    commune_parquet = commune_parquet or (s.staging_dir / "commune.parquet")
    out = out or (s.staging_dir / "commune_ips.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)

    own = con is None
    con = con or duckdb_io.connect(memory_limit=PIEZO_MEMORY_LIMIT)
    try:
        con.execute(_PZ_DDL.format(conf=_CONF_EXPR, stations=stations_parquet))
        con.execute(_STATION_COMMUNE_DDL)
        depts = [
            r[0]
            for r in con.execute(
                f"SELECT DISTINCT code_dept FROM read_parquet('{commune_parquet}') "
                "WHERE code_dept IS NOT NULL ORDER BY 1"
            ).fetchall()
        ]
        for dep in depts:
            con.execute(_SPATIAL_DEPT_SQL.format(commune=commune_parquet, dep=dep))
        n_spatial_raw = duckdb_io.scalar(con, "SELECT count(*) FROM _station_commune") or 0
        con.execute(_DEDUP_SPATIAL_SQL)  # 1 commune/station (frontières intra/inter-dept)
        n_spatial = duckdb_io.scalar(con, "SELECT count(*) FROM _station_commune") or 0
        n_dup_spatial = n_spatial_raw - n_spatial
        con.execute(_FALLBACK_SQL.format(commune=commune_parquet))
        n_host = duckdb_io.scalar(con, "SELECT count(*) FROM _station_commune") or 0
        # Représentativité : communes sans station hôte ← station la plus proche < R km.
        for dep in depts:
            con.execute(_REPR_DEPT_SQL.format(commune=commune_parquet, dep=dep, r=radius_m))
        n_repr = (duckdb_io.scalar(con, "SELECT count(*) FROM _station_commune") or 0) - n_host
        n_stations = (
            duckdb_io.scalar(con, f"SELECT count(*) FROM read_parquet('{stations_parquet}')") or 0
        )
        n_matched_bss = (
            duckdb_io.scalar(con, "SELECT count(DISTINCT code_bss) FROM _station_commune") or 0
        )
        n_orphan = n_stations - n_matched_bss
        con.execute(_COMMUNE_IPS_SQL.format(piezo_ips=piezo_ips_parquet, out=out))
        stats = con.execute(
            f"""SELECT count(*), count(DISTINCT insee), count(DISTINCT date_mois),
                       round(avg(confiance), 3)
                FROM read_parquet('{out}')"""
        ).fetchone()
        n, n_comm, n_mois, conf_moy = stats if stats else (0, 0, 0, None)
    finally:
        if own:
            con.close()
    if n_orphan:
        log.warning("commune_ips.stations_orphelines", n=n_orphan)  # COG/coords hors périmètre
    log.info(
        "staging.commune_ips",
        path=str(out),
        n_lignes=n,
        n_communes=n_comm,
        n_mois=n_mois,
        confiance_moyenne=conf_moy,
        n_rattach_spatial=n_spatial,
        n_rattach_insee=n_host - n_spatial,
        n_rattach_repr=n_repr,
        n_dup_spatial=n_dup_spatial,
        stations_orphelines=n_orphan,
    )
    return out
