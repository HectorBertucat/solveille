"""Calibration historique `H` (v2) : « la sécheresse actuelle correspond à **X %** des
situations ayant conduit à une reconnaissance Cat-Nat sécheresse ici » — lecture
**complémentaire** et **indicative** (cf. `docs/metric.md §H`, ADR-019).

Méthode (SWI seul — indice **officiel** d'instruction sécheresse ; IPS reporté) :
1. **Substrat** `commune_swi_hist` : `z_SWI` communal mensuel sur tout l'historique de
   calibration (`SWI_CALIB_FROM` →, 1990) — réutilise `build_swi_anomalie` (plancher abaissé)
   + `build_commune_swi` (poids maille↔commune statiques), **sans nouveau SQL lourd**.
2. **Sévérité-pic par évènement reconnu** : pour chaque (commune, arrêté), `s_evt = max(−z_SWI)`
   sur les mois de la période `[dat_deb, dat_fin]` (le **pic** de sécheresse qui a déclenché la
   reconnaissance ; une « situation » = un évènement reconnu).
3. **Pool** des `s_evt` par **département** (repli **national** si < `H_MIN_POOL_DEPT` évènements).
   `z_SWI` étant déjà standardisé par maille×mois, le seuil de reconnaissance est assez homogène ;
   le pool départemental capte l'hétérogénéité résiduelle (sol, densité de sinistres, admin).
4. **`H`** = CDF empirique de la sévérité courante `s_now = −z_SWI` (mois **servi**, 2017→) dans
   le pool → `commune_h.parquet (code_insee, date_mois, s_now, h_proba, h_n_events, h_pool_level)`.
   `H` **monotone croissante** en sécheresse (donc en `T`). Le gating `E>0` est appliqué au
   **mart** (M-C) ; ici on calcule pour toute commune ayant un `z_SWI`.

Caveats (affichés) : critères de reconnaissance partiellement **administratifs** ; GASPAR =
**positifs seulement** (percentile de calibration, *pas* une proba de reconnaissance) ; asymétrie
pic-de-fenêtre (`s_evt`) vs mois courant (`s_now`) — **conservative** ; non-stationnarité
climatique (comme SWI/IPS).
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from solveille.common import duckdb_io
from solveille.common.config import SWI_CALIB_FROM, get_settings
from solveille.common.geo import dept_expr_from_insee
from solveille.common.logging import get_logger
from solveille.metric.ip_rga import H_EVENT_MAX_MONTHS, H_MIN_POOL_DEPT
from solveille.transform import commune_swi, staging

log = get_logger("solveille.transform.h_calib")


def build_commune_swi_hist(
    *,
    maille_parquet: Path | None = None,
    clim_parquet: Path | None = None,
    poids_parquet: Path | None = None,
    out: Path | None = None,
    calib_from: str = SWI_CALIB_FROM,
) -> Path:
    """`commune_swi_hist.parquet` : `z_SWI` communal mensuel sur la fenêtre de calibration
    (`calib_from` →, défaut 1990) — substrat des sévérités d'évènements pour `H`.

    Réutilise les fonctions SWI existantes : `build_swi_anomalie` au **plancher abaissé**
    (`served_from=calib_from`, sortie dédiée `swi_anomalie_hist`) puis l'agrégat communal pondéré
    `build_commune_swi`. La fenêtre **servie** (2017→) reste inchangée."""
    s = get_settings()
    anomalie_hist = staging.build_swi_anomalie(
        maille_parquet=maille_parquet,
        clim_parquet=clim_parquet,
        out=s.staging_dir / "swi_anomalie_hist.parquet",
        served_from=calib_from,
    )
    return commune_swi.build_commune_swi(
        poids_parquet=poids_parquet,
        anomalie_parquet=anomalie_hist,
        out=out or (s.staging_dir / "commune_swi_hist.parquet"),
    )


#: Calcul de `H` en une passe SQL (fenêtres, pas de cross-join) : sévérité-pic par évènement →
#: pool départemental/national → CDF empirique de la sévérité courante. Placeholders : voir
#: `build_commune_h`. La sémantique « ≤ » de la CDF vient du tri `is_event DESC` aux égalités.
_COMMUNE_H_SQL = """
COPY (
  WITH ev AS (   -- 1 ligne par (commune, arrêté) : période d'évènement reconnu
    SELECT code_insee,
           e.cod_nat_catnat AS cod_nat_catnat,
           e.dat_deb        AS dat_deb,
           e.dat_fin        AS dat_fin
    FROM (SELECT code_insee, unnest(evenements) AS e FROM read_parquet('{catnat}')) _u
  ),
  ev_sev AS (   -- sévérité-pic = max(-z_SWI) sur la fenêtre d'évènement BORNÉE (z_SWI requis)
    SELECT ev.code_insee, ev.cod_nat_catnat, max(-h.z_swi) AS s_evt
    FROM ev
    JOIN read_parquet('{swi_hist}') h
      ON h.code_insee = ev.code_insee
     AND h.z_swi IS NOT NULL
     AND h.date_mois <= date_trunc('month', ev.dat_fin)
     AND h.date_mois >= greatest(
           date_trunc('month', ev.dat_deb),
           date_trunc('month', ev.dat_fin) - INTERVAL '{max_months}' MONTH)
    GROUP BY ev.code_insee, ev.cod_nat_catnat
  ),
  ev_dept AS (SELECT {dept_ins} AS pool_dept, s_evt FROM ev_sev),
  dept_n  AS (SELECT pool_dept, count(*) AS n FROM ev_dept GROUP BY pool_dept),
  pool AS (   -- pools départementaux (n >= min) + pool national (repli)
    SELECT ed.pool_dept AS pool_key, ed.s_evt
    FROM ev_dept ed JOIN dept_n dn USING (pool_dept)
    WHERE dn.n >= {min_pool}
    UNION ALL
    SELECT 'NAT' AS pool_key, s_evt FROM ev_dept
  ),
  q AS (   -- points d'évaluation : mois servi (2017->), sévérité courante
    SELECT sw.code_insee, sw.date_mois, -sw.z_swi AS s_now,
           CASE WHEN dn.n >= {min_pool} THEN {dept_sw} ELSE 'NAT' END AS pool_key
    FROM read_parquet('{swi_served}') sw
    LEFT JOIN dept_n dn ON dn.pool_dept = {dept_sw}
    WHERE sw.z_swi IS NOT NULL
  ),
  unioned AS (
    SELECT pool_key, s_evt AS sev, 1 AS is_event,
           CAST(NULL AS VARCHAR) AS code_insee, CAST(NULL AS DATE) AS date_mois,
           CAST(NULL AS DOUBLE) AS s_now
    FROM pool
    UNION ALL
    SELECT pool_key, s_now AS sev, 0 AS is_event, code_insee, date_mois, s_now
    FROM q
  ),
  scanned AS (   -- CDF empirique : nb d'evenements du pool avec sev <= s_now (<= via is_event DESC)
    SELECT *,
      sum(is_event) OVER (PARTITION BY pool_key ORDER BY sev ASC, is_event DESC
                          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS n_le,
      sum(is_event) OVER (PARTITION BY pool_key)                            AS n_total
    FROM unioned
  )
  SELECT code_insee, date_mois, s_now,
         CASE WHEN n_total > 0 THEN n_le::DOUBLE / n_total ELSE NULL END AS h_proba,
         coalesce(n_total, 0) AS h_n_events,
         CASE WHEN pool_key = 'NAT' THEN 'national' ELSE 'departement' END AS h_pool_level
  FROM scanned
  WHERE is_event = 0
) TO '{out}' (FORMAT PARQUET);
"""


def build_commune_h(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    catnat_parquet: Path | None = None,
    swi_hist_parquet: Path | None = None,
    swi_served_parquet: Path | None = None,
    out: Path | None = None,
    min_pool: int = H_MIN_POOL_DEPT,
    max_event_months: int = H_EVENT_MAX_MONTHS,
) -> Path:
    """`commune_h.parquet` : calibration `H` par commune et par mois servi (2017→).

    Pour chaque évènement reconnu (`catnat_secheresse.evenements`), extrait la **sévérité-pic**
    `max(−z_SWI)` sur sa période (bornée aux `max_event_months` mois finissant à `dat_fin`, pour
    écarter les fenêtres GASPAR aberrantes) depuis `commune_swi_hist`, poole par **département**
    (repli **national** si < `min_pool` évènements), puis évalue `H` = **CDF empirique** de la
    sévérité courante (`commune_swi` servi). Sortie : `(code_insee, date_mois, s_now, h_proba,
    h_n_events, h_pool_level)`. `H` ∈ [0,1], monotone croissante en sécheresse.
    """
    s = get_settings()
    catnat_parquet = catnat_parquet or (s.staging_dir / "catnat_secheresse.parquet")
    swi_hist_parquet = swi_hist_parquet or (s.staging_dir / "commune_swi_hist.parquet")
    swi_served_parquet = swi_served_parquet or (s.staging_dir / "commune_swi.parquet")
    for p, name in (
        (catnat_parquet, "catnat_secheresse"),
        (swi_hist_parquet, "commune_swi_hist"),
        (swi_served_parquet, "commune_swi"),
    ):
        if not p.exists():
            raise FileNotFoundError(f"{name} absent ({p}) — lance le build amont.")
    out = out or (s.staging_dir / "commune_h.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)

    own = con is None
    con = con or duckdb_io.connect()
    try:
        con.execute(
            _COMMUNE_H_SQL.format(
                catnat=catnat_parquet,
                swi_hist=swi_hist_parquet,
                swi_served=swi_served_parquet,
                out=out,
                min_pool=min_pool,
                max_months=max_event_months,
                dept_ins=dept_expr_from_insee("code_insee"),
                dept_sw=dept_expr_from_insee("sw.code_insee"),
            )
        )
        stats = con.execute(
            f"""SELECT count(*), count(DISTINCT code_insee), count(DISTINCT date_mois),
                       round(avg(h_proba), 4),
                       count(*) FILTER (WHERE h_pool_level = 'national')
                FROM read_parquet('{out}')"""
        ).fetchone()
        n, n_comm, n_mois, hmean, n_nat = stats if stats else (0, 0, 0, None, 0)
    finally:
        if own:
            con.close()
    log.info(
        "staging.commune_h",
        path=str(out),
        n_lignes=n,
        n_communes=n_comm,
        n_mois=n_mois,
        h_moyen=hmean,
        n_lignes_pool_national=n_nat,
        min_pool=min_pool,
    )
    return out
