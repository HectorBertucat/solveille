"""IPS piézométrique : parité macro `probit`↔Python, NQT centrée-réduite, gating ≥15 ans,
cohérence (mois sec ⇒ classe sèche), et rattachement commune (spatial + repli + anti-jointure).

Géométries synthétiques en EPSG:2154 (planaire, mètres), comme `test_commune_swi`.
"""

from __future__ import annotations

import statistics
from pathlib import Path

import pytest

from solveille.common import duckdb_io
from solveille.metric.ip_rga import ips_classe, ips_nqt, probit
from solveille.transform import piezo_ips
from solveille.transform.piezo_ips import _PROBIT_MACRO_SQL


def _mensuel_rows() -> list[tuple[str, str, float]]:
    """(code_bss, 'YYYY-MM-01', ngf). S1 = 20 ans (≥15) ; août 2022 = sec. S2 = 10 ans (gating)."""
    rows: list[tuple[str, str, float]] = []
    for y in range(2005, 2025):  # 20 ans
        for m in range(1, 13):
            ngf = 100.0 + 2.0 * m + 0.3 * (y - 2005)  # tendance douce + saison
            if y == 2022 and m == 8:
                ngf = 90.0  # nettement sous la distribution d'août → sécheresse marquée
            rows.append(("S1", f"{y}-{m:02d}-01", ngf))
    for y in range(2010, 2020):  # 10 ans seulement → IPS non calculé
        for m in range(1, 13):
            rows.append(("S2", f"{y}-{m:02d}-01", 50.0 + m))
    return rows


def _hist(rows: list[tuple[str, str, float]], code: str, month: int) -> list[float]:
    """Niveaux du même mois calendaire (tout l'historique) d'une station."""
    return [ngf for c, d, ngf in rows if c == code and int(d[5:7]) == month]


@pytest.fixture
def mensuel_parquet(tmp_path: Path) -> Path:
    con = duckdb_io.connect()
    try:
        vals = ", ".join(f"('{c}', DATE '{d}', {ngf})" for c, d, ngf in _mensuel_rows())
        out = tmp_path / "piezo_mensuel.parquet"
        con.execute(
            f"COPY (SELECT * FROM (VALUES {vals}) AS t(code_bss, date_mois, ngf)) "
            f"TO '{out}' (FORMAT PARQUET);"
        )
    finally:
        con.close()
    return out


def test_probit_macro_matches_python_reference() -> None:
    con = duckdb_io.connect()
    try:
        con.execute(_PROBIT_MACRO_SQL)
        for i in range(1, 100):
            p = i / 100.0
            got = con.execute("SELECT probit(?)", [p]).fetchone()[0]
            assert got == pytest.approx(probit(p), abs=1e-6)  # Acklam ↔ NormalDist
        # médiane → 0, symétrie
        assert con.execute("SELECT probit(0.5)").fetchone()[0] == pytest.approx(0.0, abs=1e-9)
    finally:
        con.close()


def test_piezo_ips_parity_and_centered_reduced(mensuel_parquet: Path, tmp_path: Path) -> None:
    rows = _mensuel_rows()
    con = duckdb_io.connect()
    try:
        # served_from très ancien → toutes les lignes sorties (pour vérifier le centré-réduit).
        out = piezo_ips.build_piezo_ips(
            con, mensuel_parquet=mensuel_parquet, out=tmp_path / "ips.parquet",
            served_from="2000-01-01",
        )
        got = con.execute(
            f"""SELECT code_bss, strftime(date_mois, '%Y-%m-%d'),
                       z_ips, ips_nqt, ips_classe, n_years
                FROM read_parquet('{out}') ORDER BY code_bss, date_mois"""
        ).fetchall()
    finally:
        con.close()

    by_key = {(c, d): (z, nqt, cl, n) for c, d, z, nqt, cl, n in got}

    # Parité SQL ↔ Python sur S1 (z plain + NQT) pour chaque mois.
    for (c, d, ngf) in rows:
        if c != "S1":
            continue
        h = _hist(rows, "S1", int(d[5:7]))
        z, nqt, cl, n = by_key[("S1", d)]
        assert n == 20
        exp_z = (ngf - statistics.fmean(h)) / statistics.stdev(h)  # stdev = stddev_samp (n-1)
        assert z == pytest.approx(exp_z, abs=1e-6)
        assert nqt == pytest.approx(ips_nqt(ngf, h), abs=1e-6)
        assert cl == ips_classe(ips_nqt(ngf, h))

    # Centré-réduit : sur un mois calendaire (août), les z_ips de S1 ont moyenne≈0, σ≈1.
    aug_z = [by_key[("S1", f"{y}-08-01")][0] for y in range(2005, 2025)]
    assert statistics.fmean(aug_z) == pytest.approx(0.0, abs=1e-9)
    assert statistics.stdev(aug_z) == pytest.approx(1.0, abs=1e-9)


def test_piezo_ips_coherence_dry_month_and_gating(mensuel_parquet: Path, tmp_path: Path) -> None:
    con = duckdb_io.connect()
    try:
        out = piezo_ips.build_piezo_ips(
            con, mensuel_parquet=mensuel_parquet, out=tmp_path / "ips.parquet",
        )  # served_from par défaut (2017→) : août 2022 est dans la fenêtre
        dry = con.execute(
            f"""SELECT z_ips, ips_classe FROM read_parquet('{out}')
                WHERE code_bss = 'S1' AND date_mois = DATE '2022-08-01'"""
        ).fetchone()
        s2 = con.execute(
            f"""SELECT count(*), count(*) FILTER (WHERE z_ips IS NOT NULL)
                FROM read_parquet('{out}') WHERE code_bss = 'S2'"""
        ).fetchone()
    finally:
        con.close()
    # cohérence : un août nettement plus sec que sa climatologie ⇒ z_ips<0 et classe sèche (0/1).
    assert dry is not None
    assert dry[0] < 0
    assert dry[1] in (0, 1)
    # gating : S2 (10 ans < 15) présent mais z_ips toujours NULL (pas d'IPS fiable).
    assert s2[0] > 0 and s2[1] == 0


# --- Rattachement commune (spatial point-dans-commune + repli code_commune_insee) ---

_C2 = "POLYGON((100000 100000, 103000 100000, 103000 103000, 100000 103000, 100000 100000))"
_COMMUNES = [
    ("C1", "99", "POLYGON((0 0, 3000 0, 3000 3000, 0 3000, 0 0))"),  # contient S1 & S5
    ("C2", "99", _C2),  # loin → S3 (hors polygones) y est rattachée par repli INSEE
]
# (code_bss, code_commune_insee, code_departement, span_annees, WKT point)
_STATIONS = [
    ("S1", "C1", "99", 20.0, "POINT(1000 1000)"),   # dans C1 (spatial) → confiance 0.6
    ("S5", "C1", "99", 30.0, "POINT(1500 1500)"),   # dans C1 (spatial) → confiance 1.0
    ("S3", "C2", "99", 30.0, "POINT(50000 50000)"),  # hors polygones → repli INSEE vers C2
    ("S4", None, "99", 30.0, "POINT(60000 60000)"),  # hors + pas d'INSEE → orpheline
]
# (code_bss, date_mois, z_ips, ips_nqt) pour août 2022
_PIEZO_IPS = [
    ("S1", "2022-08-01", -2.0, -1.7, 0),
    ("S5", "2022-08-01", 0.0, 0.0, 3),
    ("S3", "2022-08-01", -0.5, -0.4, 2),
    ("S4", "2022-08-01", -3.0, -2.5, 0),  # exclue (orpheline)
]


@pytest.fixture
def commune_fixtures(tmp_path: Path) -> dict[str, Path]:
    con = duckdb_io.connect()
    try:
        cvals = ", ".join(
            f"('{i}', '{d}', ST_AsWKB(ST_GeomFromText('{w}')))" for i, d, w in _COMMUNES
        )
        commune = tmp_path / "commune.parquet"
        con.execute(
            f"COPY (SELECT * FROM (VALUES {cvals}) AS t(code_insee, code_dept, geom_wkb)) "
            f"TO '{commune}' (FORMAT PARQUET);"
        )
        svals = ", ".join(
            f"('{b}', {'NULL' if ci is None else repr(ci)}, '{dp}', {sp}, "
            f"ST_AsWKB(ST_GeomFromText('{w}')))"
            for b, ci, dp, sp, w in _STATIONS
        )
        stations = tmp_path / "piezo_stations.parquet"
        con.execute(
            f"COPY (SELECT * FROM (VALUES {svals}) "
            f"AS t(code_bss, code_commune_insee, code_departement, span_annees, geom_wkb)) "
            f"TO '{stations}' (FORMAT PARQUET);"
        )
        ivals = ", ".join(
            f"('{b}', DATE '{d}', {z}, {nqt}, {cl})" for b, d, z, nqt, cl in _PIEZO_IPS
        )
        ips = tmp_path / "piezo_ips.parquet"
        con.execute(
            f"COPY (SELECT * FROM (VALUES {ivals}) "
            f"AS t(code_bss, date_mois, z_ips, ips_nqt, ips_classe)) "
            f"TO '{ips}' (FORMAT PARQUET);"
        )
    finally:
        con.close()
    return {"commune": commune, "stations": stations, "ips": ips}


def test_commune_ips_attachment_weighting_and_orphans(
    commune_fixtures: dict[str, Path], tmp_path: Path
) -> None:
    con = duckdb_io.connect()
    try:
        out = piezo_ips.build_commune_ips(
            con,
            stations_parquet=commune_fixtures["stations"],
            piezo_ips_parquet=commune_fixtures["ips"],
            commune_parquet=commune_fixtures["commune"],
            out=tmp_path / "commune_ips.parquet",
        )
        res = {
            i: (round(z, 4), round(c, 4), n)
            for i, z, c, n in con.execute(
                f"SELECT insee, z_ips, confiance, n_stations FROM read_parquet('{out}')"
            ).fetchall()
        }
    finally:
        con.close()
    # C1 : 2 stations (S1 conf 0.6 z=-2 ; S5 conf 1.0 z=0) → z pondéré = (0.6·-2+1·0)/1.6 = -0.75 ;
    #      confiance = meilleure station = 1.0 ; n_stations = 2.
    assert res["C1"] == (-0.75, 1.0, 2)
    # C2 : S3 rattachée par repli INSEE (hors polygone) → z=-0.5, confiance 1.0 (span 30), n=1.
    assert res["C2"] == (-0.5, 1.0, 1)
    # S4 orpheline (hors polygone + pas d'INSEE) → aucune commune (et pas de fuite de z=-3).
    assert "S4" not in res and len(res) == 2


# Représentativité : CH contient SA ; CN (sans station) à 7 km → hérite de SA, confiance décrue ;
# CF à 25 km (> rayon) → pas d'IPS.
# CH contient SA(5000,5000) ; CN centroïde d=7000 (<R) ; CF centroïde d=25000 (>R, exclue)
_REPR_COMMUNES = [
    ("CH", "99", "POLYGON((0 0, 10000 0, 10000 10000, 0 10000, 0 0))"),
    ("CN", "99", "POLYGON((11000 4000, 13000 4000, 13000 6000, 11000 6000, 11000 4000))"),
    ("CF", "99", "POLYGON((29000 4000, 31000 4000, 31000 6000, 29000 6000, 29000 4000))"),
]


def test_commune_ips_representativity_radius(tmp_path: Path) -> None:
    con = duckdb_io.connect()
    try:
        cvals = ", ".join(
            f"('{i}', '{d}', ST_AsWKB(ST_GeomFromText('{w}')))" for i, d, w in _REPR_COMMUNES
        )
        commune = tmp_path / "commune.parquet"
        con.execute(
            f"COPY (SELECT * FROM (VALUES {cvals}) AS t(code_insee, code_dept, geom_wkb)) "
            f"TO '{commune}' (FORMAT PARQUET);"
        )
        stations = tmp_path / "piezo_stations.parquet"
        con.execute(
            "COPY (SELECT * FROM (VALUES ('SA', 'CH', '99', 30.0, "
            "ST_AsWKB(ST_GeomFromText('POINT(5000 5000)')))) "
            "AS t(code_bss, code_commune_insee, code_departement, span_annees, geom_wkb)) "
            f"TO '{stations}' (FORMAT PARQUET);"
        )
        ips = tmp_path / "piezo_ips.parquet"
        con.execute(
            "COPY (SELECT * FROM (VALUES ('SA', DATE '2022-08-01', -1.0, -0.9, 1)) "
            "AS t(code_bss, date_mois, z_ips, ips_nqt, ips_classe)) "
            f"TO '{ips}' (FORMAT PARQUET);"
        )
        out = piezo_ips.build_commune_ips(
            con, stations_parquet=stations, piezo_ips_parquet=ips, commune_parquet=commune,
            out=tmp_path / "commune_ips.parquet", radius_m=10000.0,
        )
        res = {
            i: round(c, 4)
            for i, c in con.execute(
                f"SELECT insee, confiance FROM read_parquet('{out}')"
            ).fetchall()
        }
    finally:
        con.close()
    assert res["CH"] == pytest.approx(1.0)  # hôte (span 30 → f_hist 1.0, f_repr 1.0)
    assert res["CN"] == pytest.approx(0.3)  # repr : f_hist 1.0 × (1 − 7000/10000) = 0.3
    assert "CF" not in res  # > rayon → pas de station représentative
