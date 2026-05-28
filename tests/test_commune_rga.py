"""Test déterministe de l'intersection RGA ∩ commune (géométries synthétiques connues).

Trois communes carrées de 1 km² : A chevauche un aléa Moyen (moitié gauche, 0.5) et un
aléa Fort (coin, 0.2) → moyen+fort = 0.7 ; B (même dept, hors aléa) → 0 mais couverte ;
C (département sans RGA) → non couverte, classe NULL.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from solveille.common import duckdb_io
from solveille.transform.commune_rga import build_commune_rga

# Carrés en Lambert 93 (mètres). A = [0,1000]² ; B = [2000,3000]×[0,1000] ; C = comme A.
_A = "POLYGON((0 0,1000 0,1000 1000,0 1000,0 0))"
_B = "POLYGON((2000 0,3000 0,3000 1000,2000 1000,2000 0))"
_COMMUNES = f"""
  SELECT * FROM (VALUES
    ('10001', '99', ST_AsWKB(ST_GeomFromText('{_A}'))),
    ('10002', '99', ST_AsWKB(ST_GeomFromText('{_B}'))),
    ('10003', '88', ST_AsWKB(ST_GeomFromText('{_A}')))
  ) t(code_insee, code_dept, geom_wkb)
"""
# Moyen = moitié gauche de A (aire 5e5) ; Fort = coin bas-droit de A (500×400 = 2e5).
_MOYEN = "POLYGON((0 0,500 0,500 1000,0 1000,0 0))"
_FORT = "POLYGON((500 0,1000 0,1000 400,500 400,500 0))"
_RGA = f"""
  SELECT * FROM (VALUES
    ('99', 2, ST_AsWKB(ST_GeomFromText('{_MOYEN}'))),
    ('99', 3, ST_AsWKB(ST_GeomFromText('{_FORT}')))
  ) t(code_dept, niveau, geom_wkb)
"""


@pytest.fixture
def commune_rga(tmp_path: Path) -> Path:
    commune = tmp_path / "commune.parquet"
    rga = tmp_path / "rga.parquet"
    out = tmp_path / "commune_rga.parquet"
    with duckdb_io.connection() as con:
        con.execute(f"COPY ({_COMMUNES}) TO '{commune}' (FORMAT PARQUET)")
        con.execute(f"COPY ({_RGA}) TO '{rga}' (FORMAT PARQUET)")
    build_commune_rga(commune_parquet=commune, rga_parquet=rga, out=out)
    return out


def _row(path: Path, insee: str) -> tuple:
    con = duckdb_io.connect()
    return con.execute(
        f"""
        SELECT part_alea_moyen, part_alea_fort, part_alea_moyen_fort,
               classe_dominante, has_rga_coverage
        FROM read_parquet('{path}') WHERE code_insee = '{insee}'
        """
    ).fetchone()


def test_partial_overlap_exact(commune_rga: Path) -> None:
    pm, pf, pmf, cls, cov = _row(commune_rga, "10001")
    assert pm == pytest.approx(0.5)
    assert pf == pytest.approx(0.2)
    assert pmf == pytest.approx(0.7)
    assert cls == "Moyen"  # aire moyen (5e5) > aire fort (2e5)
    assert cov is True


def test_no_overlap_but_covered(commune_rga: Path) -> None:
    pm, pf, pmf, cls, cov = _row(commune_rga, "10002")
    assert (pm, pf, pmf) == pytest.approx((0.0, 0.0, 0.0))
    assert cls == "Aucun"
    assert cov is True


def test_department_without_rga(commune_rga: Path) -> None:
    pm, pf, pmf, cls, cov = _row(commune_rga, "10003")
    assert (pm, pf, pmf) == pytest.approx((0.0, 0.0, 0.0))
    assert cls is None
    assert cov is False


def test_all_communes_present(commune_rga: Path) -> None:
    con = duckdb_io.connect()
    n = con.execute(f"SELECT count(*) FROM read_parquet('{commune_rga}')").fetchone()[0]
    assert n == 3  # couverture : aucune commune perdue (LEFT JOIN)
