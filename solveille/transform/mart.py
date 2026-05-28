"""Mart servi `commune_pression` — assemble E, J et les flags par commune (v0).

LEFT JOIN depuis toutes les communes : exposition (`commune_rga`), stock + vulnérabilité
(`commune_stock`), prix (`commune_dvf`), reclassement (`commune_bascule`). Calcule **E**
(exposition) et **J** (`valeur_bati_exposee_eur`). En v0, **pas de T** → `ip_rga_score`/
`ip_rga_niveau` restent NULL. Propage les `last_updated_*` (traçabilité, garde-fou #5).
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from solveille.common import duckdb_io
from solveille.common.config import get_settings
from solveille.common.logging import get_logger
from solveille.common.raw import read_manifest
from solveille.metric.ip_rga import W_BATI, W_SURFACE

log = get_logger("solveille.transform.mart")

# E : réplique metric.ip_rga.exposition_e en SQL (poids injectés depuis ip_rga pour rester
# synchronisés). Pas d'argile (part_alea_moyen_fort=0) ⇒ E=0 (la vulnérabilité EPCI ne crée
# pas d'exposition) ; vulnérabilité NULL ⇒ exposition surfacique seule.
_E_EXPR = f"""
    CASE WHEN COALESCE(cr.part_alea_moyen_fort, 0.0) <= 0.0 THEN 0.0
         WHEN cs.part_maisons_vulnerables IS NULL
         THEN LEAST(GREATEST(cr.part_alea_moyen_fort, 0.0), 1.0)
         ELSE LEAST(GREATEST({W_SURFACE} * cr.part_alea_moyen_fort
                             + {W_BATI} * cs.part_maisons_vulnerables, 0.0), 1.0)
    END
"""

_SQL = """
COPY (
  SELECT
    c.code_insee                       AS insee,
    c.nom,
    c.code_dept,
    CAST(NULL AS DATE)                 AS date,
    {e_expr}::DOUBLE                   AS E,
    cr.part_alea_moyen,
    cr.part_alea_fort,
    cr.part_alea_moyen_fort,
    cr.classe_dominante,
    cr.has_rga_coverage,
    cs.part_maisons_vulnerables,
    cs.n_maisons_exposees,
    cs.stock_secret,
    cd.prix_median_maison_eur_m2,
    cd.surface_mediane_maison_m2,
    cd.n_tx_maison_12m,
    (cs.n_maisons_exposees * cd.surface_mediane_maison_m2
       * cd.prix_median_maison_eur_m2)::DOUBLE  AS valeur_bati_exposee_eur,
    CAST(NULL AS BIGINT)               AS n_tx_zone_exposee_12m,
    COALESCE(cb.basculement_2026, FALSE) AS basculement_2026,
    cb.rga_classe_2020,
    cb.rga_classe_2026,
    cb.bascule_type,
    CAST(NULL AS DOUBLE)               AS ip_rga_score,
    CAST(NULL AS VARCHAR)              AS ip_rga_niveau,
    {lu_admin_express}                 AS last_updated_admin_express,
    {lu_rga}                           AS last_updated_rga,
    {lu_bascule}                       AS last_updated_bascule,
    {lu_insee}                         AS last_updated_insee,
    {lu_fideli}                        AS last_updated_fideli,
    {lu_dvf}                           AS last_updated_dvf
  FROM read_parquet('{commune}') c
  LEFT JOIN read_parquet('{commune_rga}')     cr ON cr.code_insee = c.code_insee
  LEFT JOIN read_parquet('{commune_stock}')   cs ON cs.code_insee = c.code_insee
  LEFT JOIN read_parquet('{commune_dvf}')     cd ON cd.code_insee = c.code_insee
  LEFT JOIN read_parquet('{commune_bascule}') cb ON cb.code_insee = c.code_insee
) TO '{out}' (FORMAT PARQUET);
"""


def _last_updated(source: str) -> str:
    """Date de récupération (date_fetch) d'une source → littéral SQL (`'...'` ou NULL)."""
    base = get_settings().source_raw_dir(source)
    metas = sorted(base.rglob("_meta.json")) if base.exists() else []
    if not metas:
        return "NULL"
    m = read_manifest(metas[-1].parent)
    val = (m or {}).get("date_fetch")
    return f"'{val}'" if val else "NULL"


def build_commune_pression(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    staging_dir: Path | None = None,
    out: Path | None = None,
) -> Path:
    """Assemble `data/marts/commune_pression.parquet` (E, J, flags ; T/score en v1)."""
    s = get_settings()
    stg = staging_dir or s.staging_dir
    out = out or (s.marts_dir / "commune_pression.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)

    sql = _SQL.format(
        e_expr=_E_EXPR.strip(),
        commune=stg / "commune.parquet",
        commune_rga=stg / "commune_rga.parquet",
        commune_stock=stg / "commune_stock.parquet",
        commune_dvf=stg / "commune_dvf.parquet",
        commune_bascule=stg / "commune_bascule.parquet",
        out=out,
        lu_admin_express=_last_updated("admin_express"),
        lu_rga=_last_updated("rga_2026"),
        lu_bascule=_last_updated("communes_bascule"),
        lu_insee=_last_updated("insee_logement"),
        lu_fideli=_last_updated("fideli_epci"),
        lu_dvf=_last_updated("dvf"),
    )

    own = con is None
    con = con or duckdb_io.connect()
    try:
        con.execute(sql)
        stats = con.execute(
            f"""SELECT count(*),
                       count(*) FILTER (WHERE E > 0),
                       count(*) FILTER (WHERE valeur_bati_exposee_eur IS NOT NULL),
                       count(*) FILTER (WHERE basculement_2026)
                FROM read_parquet('{out}')"""
        ).fetchone()
        n, n_e, n_val, n_basc = stats if stats else (0, 0, 0, 0)
    finally:
        if own:
            con.close()
    log.info(
        "mart.commune_pression",
        path=str(out),
        n_communes=n,
        n_exposees=n_e,
        n_avec_valeur=n_val,
        n_basculees=n_basc,
    )
    return out
