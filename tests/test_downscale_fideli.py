"""Test déterministe de la descente Fideli EPCI → commune (clé stock × exposition).

EPCI synthétique de 100 maisons exposées (moyen+fort), 2 communes : A (80 maisons INSEE,
exposition 0.5 → poids 40) et B (20 maisons, exposition 1.0 → poids 20). La répartition doit
donner A=66.67, B=33.33 (Σ=100, conservatif). Vulnérabilité EPCI = 30/100 = 0.3 (appliquée
aux deux). Une commune d'un EPCI absent de Fideli → non appariée (n_maisons_exposees NULL).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from solveille.common import duckdb_io
from solveille.transform.downscale_fideli import build_commune_stock

_COMMUNE = """SELECT * FROM (VALUES
  ('10001','999999999'), ('10002','999999999'), ('10003','888888888')
) t(code_insee, siren_epci)"""
_LOGEMENT = """SELECT * FROM (VALUES
  ('10001', 80.0), ('10002', 20.0), ('10003', 50.0)
) t(code_insee, n_maisons)"""
_RGA = """SELECT * FROM (VALUES
  ('10001', 0.5), ('10002', 1.0), ('10003', 1.0)
) t(code_insee, part_alea_moyen_fort)"""
_EPCI_STOCK = """SELECT * FROM (VALUES
  ('999999999', 60, 40)
) t(siren_epci, maisons_rga2, maisons_rga3)"""
_EPCI_PERIODE = """SELECT * FROM (VALUES
  ('999999999','<1919', 20, 10), ('999999999','>2005', 40, 30)
) t(siren_epci, periode_construction, maisons_rga2, maisons_rga3)"""


@pytest.fixture
def commune_stock(tmp_path: Path) -> Path:
    paths = {}
    with duckdb_io.connection() as con:
        for name, sql in [
            ("commune", _COMMUNE),
            ("commune_logement", _LOGEMENT),
            ("commune_rga", _RGA),
            ("epci_stock", _EPCI_STOCK),
            ("epci_stock_periode", _EPCI_PERIODE),
        ]:
            p = tmp_path / f"{name}.parquet"
            con.execute(f"COPY ({sql}) TO '{p}' (FORMAT PARQUET)")
            paths[name] = p
    out = tmp_path / "commune_stock.parquet"
    build_commune_stock(
        commune_parquet=paths["commune"],
        commune_rga_parquet=paths["commune_rga"],
        commune_logement_parquet=paths["commune_logement"],
        epci_stock_parquet=paths["epci_stock"],
        epci_stock_periode_parquet=paths["epci_stock_periode"],
        out=out,
    )
    return out


def _row(path: Path, insee: str) -> tuple:
    con = duckdb_io.connect()
    return con.execute(
        f"""SELECT n_maisons_exposees, part_maisons_vulnerables, has_epci_match
            FROM read_parquet('{path}') WHERE code_insee = '{insee}'"""
    ).fetchone()


def test_downscale_proportional_and_conservative(commune_stock: Path) -> None:
    a = _row(commune_stock, "10001")
    b = _row(commune_stock, "10002")
    assert a[0] == pytest.approx(100 * 40 / 60)  # 66.67
    assert b[0] == pytest.approx(100 * 20 / 60)  # 33.33
    assert a[0] + b[0] == pytest.approx(100.0)  # conservation
    assert a[1] == pytest.approx(0.3)  # vulnérabilité EPCI appliquée
    assert b[1] == pytest.approx(0.3)


def test_unmatched_epci_is_null(commune_stock: Path) -> None:
    c = _row(commune_stock, "10003")
    assert c[0] is None  # EPCI absent de Fideli → non apparié
    assert c[2] is False
