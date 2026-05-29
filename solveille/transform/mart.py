"""Marts servis — pression RGA par commune (v1, temporel).

Deux tables (ADR-016) :
- **`commune_pression`** (statique, 1 ligne/commune) : E, J, flags + le **dernier mois**
  (`ip_rga_score`/`ip_rga_niveau`/`T_latest`/`date`). LEFT JOIN exposition (`commune_rga`),
  stock + vulnérabilité (`commune_stock`), prix (`commune_dvf`), reclassement
  (`commune_bascule`), dernier mois (`commune_pression_mensuel`).
- **`commune_pression_mensuel`** (1 ligne/(insee, mois) sur la fenêtre servie) : `z_swi`,
  `dry_swi`, `T`, `ip_rga_score`, `ip_rga_niveau`(+code), `confiance_t` (`z_ips`/`dry_ips`
  NULL en v1.0). `score = round(100·E·T^γ)` ; 5 niveaux par **quantiles nationaux** (E>0),
  poolés sur toute la fenêtre, écrits dans `seuils_niveaux.json` (exposés via `/meta`).

Propage les `last_updated_*` (traçabilité, garde-fou #5).
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

from solveille.common import duckdb_io
from solveille.common.config import get_settings
from solveille.common.logging import get_logger
from solveille.common.raw import read_manifest
from solveille.metric.ip_rga import GAIN, GAMMA, NIVEAU_LABELS, W_BATI, W_SURFACE

log = get_logger("solveille.transform.mart")

#: Quantiles nationaux délimitant les 5 niveaux (quintiles par défaut).
NIVEAU_QUANTILES = [0.2, 0.4, 0.6, 0.8]

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
    ml.date_mois                       AS date,
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
    ml.ip_rga_score                    AS ip_rga_score,
    ml.ip_rga_niveau                   AS ip_rga_niveau,
    ml.t_latest                        AS T_latest,
    {lu_admin_express}                 AS last_updated_admin_express,
    {lu_rga}                           AS last_updated_rga,
    {lu_bascule}                       AS last_updated_bascule,
    {lu_insee}                         AS last_updated_insee,
    {lu_fideli}                        AS last_updated_fideli,
    {lu_dvf}                           AS last_updated_dvf,
    {lu_swi}                           AS last_updated_swi
  FROM read_parquet('{commune}') c
  LEFT JOIN read_parquet('{commune_rga}')     cr ON cr.code_insee = c.code_insee
  LEFT JOIN read_parquet('{commune_stock}')   cs ON cs.code_insee = c.code_insee
  LEFT JOIN read_parquet('{commune_dvf}')     cd ON cd.code_insee = c.code_insee
  LEFT JOIN read_parquet('{commune_bascule}') cb ON cb.code_insee = c.code_insee
  LEFT JOIN (
    SELECT insee, date_mois, ip_rga_score, ip_rga_niveau, T AS t_latest
    FROM read_parquet('{commune_mensuel}')
    WHERE date_mois = (SELECT max(date_mois) FROM read_parquet('{commune_mensuel}'))
  ) ml ON ml.insee = c.code_insee
) TO '{out}' (FORMAT PARQUET);
"""


# Base mensuelle : E (réplique metric.exposition_e) × dry_SWI → T → score. dry_SWI =
# sigma(-GAIN·z) = 1/(1+exp(GAIN·z)). T = dry_SWI en v1.0 (IPS reporté). score borné [0,100].
_MENSUEL_BASE_SQL = """
CREATE OR REPLACE TEMP TABLE _mensuel AS
SELECT insee, date_mois, E, z_swi, dry_swi, dry_swi AS T,
       CAST(LEAST(GREATEST(round(100.0 * E * pow(dry_swi, {gamma})), 0), 100) AS INTEGER)
         AS ip_rga_score
FROM (
  SELECT sw.code_insee AS insee, sw.date_mois, sw.z_swi,
         {e_expr}::DOUBLE                                AS E,
         (1.0 / (1.0 + exp({gain} * sw.z_swi)))::DOUBLE  AS dry_swi
  FROM read_parquet('{commune_swi}') sw
  LEFT JOIN read_parquet('{commune_rga}')   cr ON cr.code_insee = sw.code_insee
  LEFT JOIN read_parquet('{commune_stock}') cs ON cs.code_insee = sw.code_insee
)
"""

# Niveaux par quantiles : E<=0 (pas d'argile / hors couverture) ⇒ NULL ; sinon bin du score.
_MENSUEL_COPY_SQL = """
COPY (
  SELECT insee, date_mois, z_swi, dry_swi,
         CAST(NULL AS DOUBLE) AS z_ips, CAST(NULL AS DOUBLE) AS dry_ips,
         T, ip_rga_score,
         CASE WHEN E <= 0 THEN NULL {code_when} END AS ip_rga_niveau_code,
         CASE WHEN E <= 0 THEN NULL {label_when} END AS ip_rga_niveau,
         1.0 AS confiance_t
  FROM _mensuel
) TO '{out}' (FORMAT PARQUET);
"""


def build_commune_pression_mensuel(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    staging_dir: Path | None = None,
    out: Path | None = None,
    seuils_out: Path | None = None,
) -> Path:
    """Assemble `data/marts/commune_pression_mensuel.parquet` (T, score, niveau par mois).

    Calcule les **seuils des 5 niveaux** = quantiles nationaux du score sur les communes
    exposées (E>0), poolés sur toute la fenêtre servie (seuils stables ⇒ couleurs comparables
    d'un mois à l'autre), et les écrit dans `marts/seuils_niveaux.json` (exposés par `/meta`).
    """
    s = get_settings()
    stg = staging_dir or s.staging_dir
    out = out or (s.marts_dir / "commune_pression_mensuel.parquet")
    seuils_out = seuils_out or (s.marts_dir / "seuils_niveaux.json")
    out.parent.mkdir(parents=True, exist_ok=True)

    own = con is None
    con = con or duckdb_io.connect()
    try:
        con.execute(
            _MENSUEL_BASE_SQL.format(
                gamma=GAMMA,
                gain=GAIN,
                e_expr=_E_EXPR.strip(),
                commune_swi=stg / "commune_swi.parquet",
                commune_rga=stg / "commune_rga.parquet",
                commune_stock=stg / "commune_stock.parquet",
            )
        )
        raw = duckdb_io.scalar(
            con,
            f"SELECT quantile_cont(ip_rga_score, {NIVEAU_QUANTILES}) FROM _mensuel WHERE E > 0",
        )
        seuils = [round(float(x)) for x in raw] if raw else [20, 40, 60, 80]
        code_when = (
            " ".join(f"WHEN ip_rga_score <= {b} THEN {i + 1}" for i, b in enumerate(seuils))
            + f" ELSE {len(seuils) + 1}"
        )
        label_when = (
            " ".join(
                f"WHEN ip_rga_score <= {b} THEN '{NIVEAU_LABELS[i]}'" for i, b in enumerate(seuils)
            )
            + f" ELSE '{NIVEAU_LABELS[len(seuils)]}'"
        )
        con.execute(_MENSUEL_COPY_SQL.format(code_when=code_when, label_when=label_when, out=out))
        seuils_out.write_text(
            json.dumps(
                {
                    "seuils": seuils,
                    "labels": list(NIVEAU_LABELS),
                    "quantiles": NIVEAU_QUANTILES,
                    "gamma": GAMMA,
                    "gain": GAIN,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        stats = con.execute(
            f"""SELECT count(*), count(DISTINCT insee), count(DISTINCT date_mois),
                       min(date_mois), max(date_mois),
                       count(*) FILTER (WHERE ip_rga_niveau IS NOT NULL)
                FROM read_parquet('{out}')"""
        ).fetchone()
        n, n_comm, n_mois, dmin, dmax, n_niv = stats if stats else (0, 0, 0, None, None, 0)
    finally:
        if own:
            con.close()
    log.info(
        "mart.commune_pression_mensuel",
        path=str(out),
        n_lignes=n,
        n_communes=n_comm,
        n_mois=n_mois,
        date_min=str(dmin),
        date_max=str(dmax),
        n_avec_niveau=n_niv,
        seuils=seuils,
    )
    return out


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
    mensuel: Path | None = None,
) -> Path:
    """Assemble `data/marts/commune_pression.parquet` (statique : E, J, flags + dernier mois).

    `mensuel` (défaut `marts/commune_pression_mensuel.parquet`) doit être construit **avant**
    (fournit `ip_rga_score`/`ip_rga_niveau`/`T_latest`/`date` du dernier mois).
    """
    s = get_settings()
    stg = staging_dir or s.staging_dir
    out = out or (s.marts_dir / "commune_pression.parquet")
    mensuel = mensuel or (s.marts_dir / "commune_pression_mensuel.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)

    sql = _SQL.format(
        e_expr=_E_EXPR.strip(),
        commune=stg / "commune.parquet",
        commune_rga=stg / "commune_rga.parquet",
        commune_stock=stg / "commune_stock.parquet",
        commune_dvf=stg / "commune_dvf.parquet",
        commune_bascule=stg / "commune_bascule.parquet",
        commune_mensuel=mensuel,
        out=out,
        lu_admin_express=_last_updated("admin_express"),
        lu_rga=_last_updated("rga_2026"),
        lu_bascule=_last_updated("communes_bascule"),
        lu_insee=_last_updated("insee_logement"),
        lu_fideli=_last_updated("fideli_epci"),
        lu_dvf=_last_updated("dvf"),
        lu_swi=_last_updated("swi_catnat"),
    )

    own = con is None
    con = con or duckdb_io.connect()
    try:
        con.execute(sql)
        stats = con.execute(
            f"""SELECT count(*),
                       count(*) FILTER (WHERE E > 0),
                       count(*) FILTER (WHERE valeur_bati_exposee_eur IS NOT NULL),
                       count(*) FILTER (WHERE basculement_2026),
                       count(*) FILTER (WHERE ip_rga_niveau IS NOT NULL)
                FROM read_parquet('{out}')"""
        ).fetchone()
        n, n_e, n_val, n_basc, n_niv = stats if stats else (0, 0, 0, 0, 0)
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
        n_avec_niveau=n_niv,
    )
    return out
