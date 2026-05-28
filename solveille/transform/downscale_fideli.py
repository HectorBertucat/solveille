"""Descente Fideli EPCI → commune : stock de maisons exposées + vulnérabilité du bâti.

Fideli ne donne le stock de maisons individuelles exposées (moyen+fort) qu'à la maille
**EPCI** (zonage BRGM 2020). On le redescend à la commune par une clé documentée combinant
le **parc de maisons INSEE** et la **localisation de l'argile** (part d'aléa moyen+fort) :

    w_c = n_maisons_c (INSEE) × part_alea_moyen_fort_c
    n_maisons_exposees_c = exposees_EPCI × w_c / Σ_{c'∈EPCI} w_{c'}

→ **conservatif** (la somme communale reconstitue le total EPCI) et cohérent avec E. Se
réduit au prorata du stock si l'exposition est uniforme dans l'EPCI. Approximation assumée
(ADR-001/013/014) ; le millésime EPCI (Fideli 2021) peut différer du COG commune (2026) →
les communes sans EPCI apparié ont `n_maisons_exposees` NULL (rapporté).

`part_maisons_vulnerables` (EPCI, appliqué à ses communes) = part des maisons exposées
moyen+fort **construites avant 1990** (proxy de vulnérabilité — les maisons anciennes,
sans précautions géotechniques RGA, sont plus sensibles).
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from solveille.common import duckdb_io
from solveille.common.config import get_settings
from solveille.common.logging import get_logger

log = get_logger("solveille.transform.downscale_fideli")

#: Tranches Fideli antérieures à ~1990 (maisons plus vulnérables au RGA).
VULN_PERIODS = ("<1919", "1919-1945", "1945-1970", "1970-1990")

_SQL = """
COPY (
  WITH epci_vuln AS (
    SELECT siren_epci,
           SUM(COALESCE(maisons_rga2, 0) + COALESCE(maisons_rga3, 0))
             FILTER (WHERE periode_construction IN {vuln_periods}) AS vuln_mf,
           SUM(COALESCE(maisons_rga2, 0) + COALESCE(maisons_rga3, 0)) AS tot_mf
    FROM read_parquet('{epci_periode}') GROUP BY siren_epci
  ),
  epci_tot AS (
    SELECT siren_epci,
           COALESCE(maisons_rga2, 0) + COALESCE(maisons_rga3, 0) AS exposees_mf,
           (maisons_rga2 IS NULL OR maisons_rga3 IS NULL) AS stock_secret
    FROM read_parquet('{epci_stock}')
  ),
  cw AS (  -- poids commune = parc de maisons (INSEE) × exposition (part aléa moyen+fort)
    SELECT c.code_insee, c.siren_epci,
           COALESCE(cl.n_maisons, 0) * COALESCE(cr.part_alea_moyen_fort, 0) AS w
    FROM read_parquet('{commune}') c
    LEFT JOIN read_parquet('{commune_logement}') cl USING (code_insee)
    LEFT JOIN read_parquet('{commune_rga}') cr USING (code_insee)
  ),
  ew AS (SELECT siren_epci, SUM(w) AS sw FROM cw GROUP BY siren_epci)
  SELECT
    cw.code_insee,
    cw.siren_epci,
    CASE WHEN et.exposees_mf IS NULL THEN NULL
         WHEN ew.sw > 0 THEN et.exposees_mf * cw.w / ew.sw
         ELSE 0.0 END                                    AS n_maisons_exposees,
    CASE WHEN ev.tot_mf > 0 THEN ev.vuln_mf::DOUBLE / ev.tot_mf
         ELSE NULL END                                   AS part_maisons_vulnerables,
    COALESCE(et.stock_secret, FALSE)                     AS stock_secret,
    (et.exposees_mf IS NOT NULL)                         AS has_epci_match
  FROM cw
  LEFT JOIN ew USING (siren_epci)
  LEFT JOIN epci_tot et ON et.siren_epci = cw.siren_epci
  LEFT JOIN epci_vuln ev ON ev.siren_epci = cw.siren_epci
) TO '{out}' (FORMAT PARQUET);
"""


def build_commune_stock(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    commune_parquet: Path | None = None,
    commune_rga_parquet: Path | None = None,
    commune_logement_parquet: Path | None = None,
    epci_stock_parquet: Path | None = None,
    epci_stock_periode_parquet: Path | None = None,
    out: Path | None = None,
) -> Path:
    """Calcule `data/staging/commune_stock.parquet` (maisons exposées + vulnérabilité)."""
    s = get_settings()
    commune_parquet = commune_parquet or (s.staging_dir / "commune.parquet")
    commune_rga_parquet = commune_rga_parquet or (s.staging_dir / "commune_rga.parquet")
    commune_logement_parquet = commune_logement_parquet or (
        s.staging_dir / "commune_logement.parquet"
    )
    epci_stock_parquet = epci_stock_parquet or (s.staging_dir / "epci_stock.parquet")
    epci_stock_periode_parquet = epci_stock_periode_parquet or (
        s.staging_dir / "epci_stock_periode.parquet"
    )
    out = out or (s.staging_dir / "commune_stock.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)

    vuln_periods = "('" + "', '".join(VULN_PERIODS) + "')"
    own = con is None
    con = con or duckdb_io.connect()
    try:
        con.execute(
            _SQL.format(
                vuln_periods=vuln_periods,
                epci_periode=epci_stock_periode_parquet,
                epci_stock=epci_stock_parquet,
                commune=commune_parquet,
                commune_logement=commune_logement_parquet,
                commune_rga=commune_rga_parquet,
                out=out,
            )
        )
        n = duckdb_io.scalar(con, f"SELECT count(*) FROM read_parquet('{out}')")
        orphans = duckdb_io.scalar(
            con, f"SELECT count(*) FILTER (WHERE NOT has_epci_match) FROM read_parquet('{out}')"
        )
        total_exposees = duckdb_io.scalar(
            con, f"SELECT round(sum(n_maisons_exposees)) FROM read_parquet('{out}')"
        )
    finally:
        if own:
            con.close()
    if orphans:
        log.warning("commune_stock.epci_orphans", n=orphans)  # COG 2026 ↔ EPCI Fideli 2021
    log.info(
        "staging.commune_stock",
        path=str(out),
        n_communes=n,
        n_orphelins_epci=orphans,
        total_maisons_exposees=total_exposees,
    )
    return out
