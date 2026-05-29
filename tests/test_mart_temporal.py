"""Propriétés du mart mensuel `commune_pression_mensuel` (docs/metric.md §11) :
monotonie (plus sec ⇒ score ≥ à E fixe), cohérence temporelle (mois sec > mois humide),
couverture (toutes les communes ont un T chaque mois), gating E=0 ⇒ score 0 / niveau NULL.

Commune X : E=0.5 (aléa 0.5, vuln inconnue) sur 3 mois z = +2 (humide) / 0 / -2 (sec).
Commune Z : E=0 (aléa 0) → score 0, niveau NULL quel que soit le mois.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from solveille.common import duckdb_io
from solveille.metric.ip_rga import dry_intensity, ip_rga_score
from solveille.transform.mart import build_commune_pression_mensuel

_TABLES = {
    "commune": "SELECT * FROM (VALUES ('X','99','X'),('Z','99','Z')) t(code_insee,code_dept,nom)",
    "commune_rga": """SELECT * FROM (VALUES
        ('X', 0.3, 0.2, 0.5, TRUE, 'Moyen'),
        ('Z', 0.0, 0.0, 0.0, TRUE, 'Aucun')
      ) t(code_insee, part_alea_moyen, part_alea_fort, part_alea_moyen_fort,
          has_rga_coverage, classe_dominante)""",
    "commune_stock": """SELECT * FROM (VALUES ('Y', 0.5, 1.0, FALSE))
      t(code_insee, part_maisons_vulnerables, n_maisons_exposees, stock_secret)""",
    # z_SWI : +2 (humide) → 0 → -2 (sec). Mois croissant = de plus en plus sec.
    "commune_swi": """SELECT * FROM (VALUES
        ('X', DATE '2024-06-01',  2.0, 1), ('X', DATE '2024-07-01', 0.0, 1),
        ('X', DATE '2024-08-01', -2.0, 1),
        ('Z', DATE '2024-06-01',  2.0, 1), ('Z', DATE '2024-07-01', 0.0, 1),
        ('Z', DATE '2024-08-01', -2.0, 1)
      ) t(code_insee, date_mois, z_swi, n_mailles)""",
}


@pytest.fixture
def mensuel(tmp_path: Path) -> Path:
    stg = tmp_path / "staging"
    stg.mkdir()
    with duckdb_io.connection() as con:
        for name, sql in _TABLES.items():
            con.execute(f"COPY ({sql}) TO '{stg / (name + '.parquet')}' (FORMAT PARQUET)")
    out = tmp_path / "mensuel.parquet"
    build_commune_pression_mensuel(staging_dir=stg, out=out, seuils_out=tmp_path / "s.json")
    return out


def _scores(path: Path, insee: str) -> dict[str, int]:
    con = duckdb_io.connect()
    try:
        rows = con.execute(
            f"SELECT date_mois::VARCHAR, ip_rga_score FROM read_parquet('{path}') "
            f"WHERE insee = '{insee}' ORDER BY date_mois"
        ).fetchall()
    finally:
        con.close()
    return {d: s for d, s in rows}


def test_monotonic_and_temporal_coherence(mensuel: Path) -> None:
    s = _scores(mensuel, "X")
    # à E fixe, plus sec (z↓) ⇒ score ≥ : humide (juin) < normal (juil) < sec (août)
    assert s["2024-06-01"] < s["2024-07-01"] < s["2024-08-01"]
    # le mois sec connu ressort strictement au-dessus du mois humide (cohérence temporelle)
    assert s["2024-08-01"] > s["2024-06-01"]


def test_score_matches_pure_function(mensuel: Path) -> None:
    s = _scores(mensuel, "X")
    assert s["2024-08-01"] == ip_rga_score(0.5, dry_intensity(-2.0))  # sec
    assert s["2024-06-01"] == ip_rga_score(0.5, dry_intensity(2.0))  # humide


def test_e_zero_gating_all_months(mensuel: Path) -> None:
    con = duckdb_io.connect()
    try:
        rows = con.execute(
            f"SELECT ip_rga_score, ip_rga_niveau FROM read_parquet('{mensuel}') WHERE insee='Z'"
        ).fetchall()
    finally:
        con.close()
    assert len(rows) == 3
    assert all(score == 0 and niveau is None for score, niveau in rows)  # E=0 ⇒ 0, pas de niveau


def test_coverage_all_communes_all_months(mensuel: Path) -> None:
    con = duckdb_io.connect()
    try:
        n, n_comm, n_mois, n_t_null = con.execute(
            f"""SELECT count(*), count(DISTINCT insee), count(DISTINCT date_mois),
                       count(*) FILTER (WHERE T IS NULL)
                FROM read_parquet('{mensuel}')"""
        ).fetchone()
    finally:
        con.close()
    assert n == n_comm * n_mois == 6  # 2 communes × 3 mois, aucune ligne manquante
    assert n_t_null == 0  # 100 % des communes ont un T (via SWI)


def test_niveau_present_for_exposed(mensuel: Path) -> None:
    con = duckdb_io.connect()
    try:
        n_niv = con.execute(
            f"SELECT count(*) FROM read_parquet('{mensuel}') "
            f"WHERE insee='X' AND ip_rga_niveau IS NOT NULL"
        ).fetchone()[0]
    finally:
        con.close()
    assert n_niv == 3  # commune exposée : un niveau chaque mois
