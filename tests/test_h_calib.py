"""Tests de la calibration `H` (v2) : helpers metric (sévérité, CDF empirique) + le calcul
SQL `build_commune_h` (sévérité-pic par évènement → pool dept/national → CDF).

Scénario synthétique (vérifiable à la main), `min_pool=3` :
- dépt 31 : 3 évènements (s_evt = 2.0 / 1.0 / 3.0) → pool **départemental** {1,2,3}.
- dépt 09 : 1 évènement (s_evt = 0.5) < min_pool → repli **national** {0.5,1,2,3}.
On contrôle `H∈[0,1]`, la **monotonie** (sec ⇒ `H`↑), la sémantique **≤** (égalité comptée),
le **pooling** dept↔national, et la **parité** avec `metric.h_empirical_cdf`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pytest

from solveille.common import duckdb_io
from solveille.metric import ip_rga as M
from solveille.transform import h_calib, staging

# --- helpers metric (purs) ---------------------------------------------------------------


def test_severite_signe() -> None:
    assert M.severite(-2.0) == 2.0  # sec ⇒ sévérité positive
    assert M.severite(1.5) == -1.5  # humide ⇒ négative
    assert M.severite(None) is None


def test_h_empirical_cdf_bornes_et_le() -> None:
    pool = [1.0, 2.0, 3.0]
    assert M.h_empirical_cdf(-1.0, pool) == 0.0  # plus sec qu'aucune situation reconnue
    assert M.h_empirical_cdf(5.0, pool) == 1.0  # plus sec que toutes
    assert M.h_empirical_cdf(2.0, pool) == pytest.approx(2 / 3)  # égalité comptée (<=)
    assert M.h_empirical_cdf(0.0, []) is None  # pool vide
    assert M.h_empirical_cdf(None, pool) is None


def test_h_empirical_cdf_monotone() -> None:
    pool = [3.0, 1.0, 2.0, 0.5]
    vals = [M.h_empirical_cdf(s, pool) for s in (-2.0, 0.0, 1.0, 2.5, 4.0)]
    assert vals == sorted(vals)  # croissante en sévérité


# --- intégration build_commune_h ---------------------------------------------------------

_HEADER = (
    "cod_nat_catnat;cod_commune;lib_commune;num_risque_jo;lib_risque_jo;"
    "dat_deb;dat_fin;dat_pub_arrete;dat_pub_jo;dat_maj"
)
# 1 évènement par commune ; dat_deb=dat_fin=mois de l'évènement (pic sur un seul mois).
_EVENTS = [
    ("A", "31001", "2003-08-01", "2003-12-01"),
    ("B", "31002", "2018-08-01", "2018-12-01"),
    ("C", "31003", "2022-08-01", "2022-12-01"),
    ("D", "09001", "2003-08-01", "2003-12-01"),
]


def _catnat(tmp_path: Path) -> Path:
    def row(cod: str, insee: str, deb: str, pub: str) -> str:
        return ";".join(
            [
                cod,
                insee,
                "C",
                "SEC",
                "Sécheresse",
                f"{deb} 00:00:00",
                f"{deb} 00:00:00",
                f"{pub} 00:00:00",
                f"{pub} 00:00:00",
                f"{pub} 00:00:00",
            ]
        )

    csv = tmp_path / "catnat_gaspar.csv"
    csv.write_text(_HEADER + "\n" + "\n".join(row(*e) for e in _EVENTS) + "\n", encoding="utf-8")
    return staging.build_catnat_secheresse(
        raw_csv=csv,
        out=tmp_path / "catnat.parquet",
        commune_parquet=tmp_path / "_no.parquet",
        departements=[],
    )


def _parquet(con: duckdb.DuckDBPyConnection, path: Path, values_sql: str) -> Path:
    con.execute(f"COPY ({values_sql}) TO '{path}' (FORMAT PARQUET)")
    return path


def _swi_sql(rows: list[tuple[str, str, str]]) -> str:
    """SELECT VALUES (code_insee, date_mois, z_swi) ; `z` est un littéral SQL (ex. '-2.0')."""
    vals = ", ".join(f"('{i}', DATE '{m}', {z})" for i, m, z in rows)
    return f"SELECT * FROM (VALUES {vals}) t(code_insee, date_mois, z_swi)"


@pytest.fixture
def inputs(tmp_path: Path) -> dict[str, Path]:
    con = duckdb_io.connect()
    try:
        catnat = _catnat(tmp_path)
        # z_SWI historique sur les mois d'évènement (s_evt = -z_swi du mois).
        swi_hist = _parquet(
            con,
            tmp_path / "swi_hist.parquet",
            "SELECT * FROM (VALUES "
            "('31001', DATE '2003-08-01', -2.0), "
            "('31002', DATE '2018-08-01', -1.0), "
            "('31003', DATE '2022-08-01', -3.0), "
            "('09001', DATE '2003-08-01', -0.5)) t(code_insee, date_mois, z_swi)",
        )
        # points servis (2017->) : sévérité courante s_now = -z_swi ; 1 z_swi NULL (à exclure).
        swi_served = _parquet(
            con,
            tmp_path / "swi_served.parquet",
            "SELECT * FROM (VALUES "
            "('31001', DATE '2023-05-01', -3.5), "
            "('31001', DATE '2023-06-01',  1.0), "
            "('31001', DATE '2023-07-01', -2.5), "
            "('31002', DATE '2024-08-01', -2.0), "
            "('09001', DATE '2023-07-01', -1.5), "
            "('31003', DATE '2023-07-01', CAST(NULL AS DOUBLE))) t(code_insee, date_mois, z_swi)",
        )
    finally:
        con.close()
    return {"catnat": catnat, "swi_hist": swi_hist, "swi_served": swi_served}


def _h_rows(out: Path) -> dict[tuple[str, str], tuple[Any, ...]]:
    con = duckdb_io.connect()
    try:
        rows = con.execute(
            f"SELECT code_insee, CAST(date_mois AS VARCHAR), h_proba, h_n_events, h_pool_level "
            f"FROM read_parquet('{out}') ORDER BY code_insee, date_mois"
        ).fetchall()
    finally:
        con.close()
    return {(r[0], r[1]): (r[2], r[3], r[4]) for r in rows}


def test_commune_h_valeurs_pooling_et_le(inputs: dict[str, Path], tmp_path: Path) -> None:
    out = h_calib.build_commune_h(
        catnat_parquet=inputs["catnat"],
        swi_hist_parquet=inputs["swi_hist"],
        swi_served_parquet=inputs["swi_served"],
        out=tmp_path / "commune_h.parquet",
        min_pool=3,
    )
    h = _h_rows(out)
    # dépt 31 : pool départemental {1,2,3}, n=3
    assert h[("31001", "2023-05-01")] == (pytest.approx(1.0), 3, "departement")  # s_now=3.5
    assert h[("31001", "2023-06-01")] == (pytest.approx(0.0), 3, "departement")  # s_now=-1 (humide)
    assert h[("31001", "2023-07-01")][0] == pytest.approx(2 / 3)  # s_now=2.5 → {1,2}
    assert h[("31002", "2024-08-01")][0] == pytest.approx(2 / 3)  # s_now=2.0 → <= compte l'égalité
    # dépt 09 : 1 évènement < min_pool → repli national {0.5,1,2,3}, n=4
    assert h[("09001", "2023-07-01")] == (pytest.approx(0.5), 4, "national")  # s_now=1.5 → {0.5,1}
    # z_swi NULL → pas de ligne H
    assert ("31003", "2023-07-01") not in h


def test_commune_h_monotone_en_secheresse(inputs: dict[str, Path], tmp_path: Path) -> None:
    out = h_calib.build_commune_h(
        catnat_parquet=inputs["catnat"],
        swi_hist_parquet=inputs["swi_hist"],
        swi_served_parquet=inputs["swi_served"],
        out=tmp_path / "commune_h.parquet",
        min_pool=3,
    )
    h = _h_rows(out)
    # 31001 : juin (humide, s_now=-1) < juillet (s_now=2.5) < mai (s_now=3.5) en sécheresse
    juin = h[("31001", "2023-06-01")][0]
    juil = h[("31001", "2023-07-01")][0]
    mai = h[("31001", "2023-05-01")][0]
    assert juin < juil < mai or juin <= juil <= mai  # H croît avec la sécheresse
    assert juin == pytest.approx(0.0) and mai == pytest.approx(1.0)


def test_commune_h_borne_0_1(inputs: dict[str, Path], tmp_path: Path) -> None:
    out = h_calib.build_commune_h(
        catnat_parquet=inputs["catnat"],
        swi_hist_parquet=inputs["swi_hist"],
        swi_served_parquet=inputs["swi_served"],
        out=tmp_path / "commune_h.parquet",
        min_pool=3,
    )
    con = duckdb_io.connect()
    try:
        bad = con.execute(
            f"SELECT count(*) FROM read_parquet('{out}') WHERE h_proba < 0 OR h_proba > 1"
        ).fetchone()[0]
    finally:
        con.close()
    assert bad == 0


def test_commune_h_parite_python(inputs: dict[str, Path], tmp_path: Path) -> None:
    # le pool départemental 31 = {2.0, 1.0, 3.0} ; parité SQL ↔ metric.h_empirical_cdf
    out = h_calib.build_commune_h(
        catnat_parquet=inputs["catnat"],
        swi_hist_parquet=inputs["swi_hist"],
        swi_served_parquet=inputs["swi_served"],
        out=tmp_path / "commune_h.parquet",
        min_pool=3,
    )
    h = _h_rows(out)
    pool31 = [2.0, 1.0, 3.0]
    assert h[("31001", "2023-07-01")][0] == pytest.approx(M.h_empirical_cdf(2.5, pool31))
    assert h[("31002", "2024-08-01")][0] == pytest.approx(M.h_empirical_cdf(2.0, pool31))


# --- cas limites (suggérés par metric-validator) -----------------------------------------


def _catnat_from(tmp_path: Path, events: list[tuple[str, str, str, str]], name: str) -> Path:
    """Écrit un `catnat_gaspar.csv` synthétique (events = (cod, insee, dat_deb, dat_fin)) et
    le passe par le vrai producteur staging → parquet `catnat_secheresse`."""

    def row(cod: str, insee: str, deb: str, fin: str) -> str:
        return ";".join(
            [
                cod,
                insee,
                "C",
                "SEC",
                "Sécheresse",
                f"{deb} 00:00:00",
                f"{fin} 00:00:00",
                f"{fin} 00:00:00",
                f"{fin} 00:00:00",
                f"{fin} 00:00:00",
            ]
        )

    csv = tmp_path / f"{name}.csv"
    csv.write_text(_HEADER + "\n" + "\n".join(row(*e) for e in events) + "\n", encoding="utf-8")
    return staging.build_catnat_secheresse(
        raw_csv=csv,
        out=tmp_path / f"{name}.parquet",
        commune_parquet=tmp_path / "_no.parquet",
        departements=[],
    )


def test_commune_h_cap_fenetre_24_mois(tmp_path: Path) -> None:
    # évènement long (2000→2003, 41 mois) : pic réel -3.0 en 2000-03 (HORS des 24 derniers mois
    # avant dat_fin=2003-06, coupure 2001-06) et pic modéré -1.0 en 2002-08 (DANS la fenêtre).
    catnat = _catnat_from(tmp_path, [("L", "31009", "2000-01-01", "2003-06-01")], "catnat_long")
    con = duckdb_io.connect()
    try:
        swi_hist = _parquet(
            con,
            tmp_path / "hist.parquet",
            _swi_sql([("31009", "2000-03-01", "-3.0"), ("31009", "2002-08-01", "-1.0")]),
        )
        swi_served = _parquet(
            con, tmp_path / "served.parquet", _swi_sql([("31009", "2023-07-01", "-2.0")])
        )
    finally:
        con.close()
    # cap 24 mois → pool = {1.0} (pic -3.0 ignoré) ; s_now=2.0 ≥ 1.0 → H=1.0
    out = h_calib.build_commune_h(
        catnat_parquet=catnat,
        swi_hist_parquet=swi_hist,
        swi_served_parquet=swi_served,
        out=tmp_path / "h_cap.parquet",
        min_pool=1,
        max_event_months=24,
    )
    assert _h_rows(out)[("31009", "2023-07-01")][0] == pytest.approx(1.0)
    # sans cap effectif (120 mois) → pool = {3.0} ; s_now=2.0 < 3.0 → H=0.0 (param bien propagé)
    out2 = h_calib.build_commune_h(
        catnat_parquet=catnat,
        swi_hist_parquet=swi_hist,
        swi_served_parquet=swi_served,
        out=tmp_path / "h_nocap.parquet",
        min_pool=1,
        max_event_months=120,
    )
    assert _h_rows(out2)[("31009", "2023-07-01")][0] == pytest.approx(0.0)


def test_commune_h_dept_sans_evenement_repli_national(tmp_path: Path) -> None:
    # arrêtés seulement en dépt 31 ; une commune servie de dépt 75 (absente de dept_n) → 'NAT'.
    catnat = _catnat_from(tmp_path, [("A", "31001", "2003-08-01", "2003-08-01")], "catnat_31")
    con = duckdb_io.connect()
    try:
        swi_hist = _parquet(
            con, tmp_path / "hist.parquet", _swi_sql([("31001", "2003-08-01", "-2.0")])
        )
        swi_served = _parquet(
            con, tmp_path / "served.parquet", _swi_sql([("75056", "2023-07-01", "-3.0")])
        )
    finally:
        con.close()
    out = h_calib.build_commune_h(
        catnat_parquet=catnat,
        swi_hist_parquet=swi_hist,
        swi_served_parquet=swi_served,
        out=tmp_path / "h.parquet",
        min_pool=1,
    )
    row = _h_rows(out)[("75056", "2023-07-01")]
    assert row[2] == "national" and row[1] == 1  # repli national (dn.n NULL), pool {2.0}


def test_commune_h_pool_vide_nul(tmp_path: Path) -> None:
    # évènement sans z_SWI mesurable (commune absente du substrat) → pool vide → H NULL, n=0.
    catnat = _catnat_from(tmp_path, [("Z", "31099", "2003-08-01", "2003-08-01")], "catnat_z")
    con = duckdb_io.connect()
    try:
        swi_hist = _parquet(  # aucune ligne pour 31099
            con, tmp_path / "hist.parquet", _swi_sql([("31001", "2003-08-01", "-2.0")])
        )
        swi_served = _parquet(
            con, tmp_path / "served.parquet", _swi_sql([("31001", "2023-07-01", "-1.0")])
        )
    finally:
        con.close()
    out = h_calib.build_commune_h(
        catnat_parquet=catnat,
        swi_hist_parquet=swi_hist,
        swi_served_parquet=swi_served,
        out=tmp_path / "h.parquet",
        min_pool=1,
    )
    h_proba, n_events, _ = _h_rows(out)[("31001", "2023-07-01")]
    assert h_proba is None and n_events == 0  # pas de crash / division par zéro
