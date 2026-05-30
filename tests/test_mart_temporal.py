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
from solveille.metric.ip_rga import W_IPS_MAX, dry_intensity, ip_rga_score, tension_t
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


# IPS communal pour X : juin sec (z_ips −2, vs SWI humide) → relève T ; août humide (z_ips +2,
# vs SWI sec) → abaisse T. confiance 1.0 ⇒ w_ips = W_IPS_MAX. (Z sans IPS : repli SWI.)
_COMMUNE_IPS = """SELECT * FROM (VALUES
    ('X', DATE '2024-06-01', -2.0, -2.0, 1.0, 1),
    ('X', DATE '2024-07-01',  0.0,  0.0, 1.0, 1),
    ('X', DATE '2024-08-01',  2.0,  2.0, 1.0, 1)
  ) t(insee, date_mois, z_ips, ips_nqt, confiance, n_stations)"""


def _build_mensuel(tmp_path: Path, *, with_ips: bool) -> Path:
    stg = tmp_path / "staging"
    stg.mkdir(exist_ok=True)
    with duckdb_io.connection() as con:
        for name, sql in _TABLES.items():
            con.execute(f"COPY ({sql}) TO '{stg / (name + '.parquet')}' (FORMAT PARQUET)")
        if with_ips:
            ci = stg / "commune_ips.parquet"
            con.execute(f"COPY ({_COMMUNE_IPS}) TO '{ci}' (FORMAT PARQUET)")
    out = tmp_path / "mensuel.parquet"
    build_commune_pression_mensuel(staging_dir=stg, out=out, seuils_out=tmp_path / "s.json")
    return out


@pytest.fixture
def mensuel(tmp_path: Path) -> Path:
    return _build_mensuel(tmp_path, with_ips=False)


@pytest.fixture
def mensuel_ips(tmp_path: Path) -> Path:
    return _build_mensuel(tmp_path, with_ips=True)


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


def test_no_ips_confiance_zero_and_t_equals_dry_swi(mensuel: Path) -> None:
    # Sans commune_ips : T = dry_SWI (repli universel) et confiance_t = 0 (pas de corroboration).
    con = duckdb_io.connect()
    try:
        rows = con.execute(
            f"SELECT T, dry_swi, z_ips, dry_ips, confiance_t FROM read_parquet('{mensuel}') "
            f"WHERE insee='X'"
        ).fetchall()
    finally:
        con.close()
    for t, dsw, z_ips, dry_ips, ct in rows:
        assert t == pytest.approx(dsw)
        assert z_ips is None and dry_ips is None and ct == 0.0


def test_ips_blended_into_t_and_confiance(mensuel_ips: Path) -> None:
    con = duckdb_io.connect()
    try:
        rows = {
            d: (t, ct, dsw, zi)
            for d, t, ct, dsw, zi in con.execute(
                f"SELECT date_mois::VARCHAR, T, confiance_t, dry_swi, z_ips "
                f"FROM read_parquet('{mensuel_ips}') WHERE insee='X' ORDER BY date_mois"
            ).fetchall()
        }
    finally:
        con.close()
    w_ips = 1.0 * W_IPS_MAX  # confiance 1.0 (fixture)
    # Parité SQL ↔ metric.tension_t (juin : SWI humide z=+2, IPS sec z=−2 ; août inverse).
    assert rows["2024-06-01"][0] == pytest.approx(
        tension_t(dry_intensity(2.0), dry_intensity(-2.0), w_ips=w_ips)
    )
    assert rows["2024-08-01"][0] == pytest.approx(
        tension_t(dry_intensity(-2.0), dry_intensity(2.0), w_ips=w_ips)
    )
    # IPS plus sec que SWI en juin (humide) ⇒ T relevé au-dessus de dry_SWI ; inverse en août.
    assert rows["2024-06-01"][0] > rows["2024-06-01"][2]
    assert rows["2024-08-01"][0] < rows["2024-08-01"][2]
    # confiance_t = part IPS dans T = w_ips/(w_swi+w_ips), exposée et bornée.
    assert rows["2024-06-01"][1] == pytest.approx(w_ips / (1.0 + w_ips))
    assert all(0.0 <= ct <= 1.0 for _, ct, _, _ in rows.values())
    assert rows["2024-06-01"][3] == pytest.approx(-2.0)  # z_ips rempli (plus NULL)


def test_ips_classe_binned_in_mart(mensuel_ips: Path) -> None:
    con = duckdb_io.connect()
    try:
        rows = {
            d: c
            for d, c in con.execute(
                f"SELECT date_mois::VARCHAR, ips_classe FROM read_parquet('{mensuel_ips}') "
                f"WHERE insee='X' ORDER BY date_mois"
            ).fetchall()
        }
    finally:
        con.close()
    # ips_nqt −2 → classe 0 (Très bas/sec) ; 0 → classe 3 (Autour moyenne) ; +2 → classe 6 (humide).
    assert rows["2024-06-01"] == 0
    assert rows["2024-07-01"] == 3
    assert rows["2024-08-01"] == 6


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
