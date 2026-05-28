"""Contrats sur le staging Fideli EPCI (pivot, secret→NULL) sur fixtures offline."""

from __future__ import annotations

from pathlib import Path

import pytest

from solveille.common import duckdb_io
from solveille.transform import staging

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def epci_stock(tmp_path: Path) -> Path:
    out = tmp_path / "epci_stock.parquet"
    staging.build_epci_stock(csv=FIX / "fideli_par_epci_sample.csv", out=out)
    return out


def test_pivot_and_secret(epci_stock: Path) -> None:
    con = duckdb_io.connect()
    cols = {
        c[0] for c in con.execute(f"SELECT * FROM read_parquet('{epci_stock}') LIMIT 0").description
    }
    assert {
        "siren_epci",
        "maisons_rga1",
        "maisons_rga2",
        "maisons_rga3",
        "surface_rga1_km2",
        "has_secret",
    }.issubset(cols)
    # EPCI avec un 'secret' sur RGA3 maisons
    m1, m2, m3, s1, secret = con.execute(
        f"""SELECT maisons_rga1, maisons_rga2, maisons_rga3, surface_rga1_km2, has_secret
            FROM read_parquet('{epci_stock}') WHERE siren_epci = '200000172'"""
    ).fetchone()
    assert (m1, m2) == (461, 59)
    assert m3 is None  # 'secret' → NULL
    assert s1 == pytest.approx(69.96)
    assert secret is True
    # EPCI sans secret
    m1b, m3b, secretb = con.execute(
        f"""SELECT maisons_rga1, maisons_rga3, has_secret
            FROM read_parquet('{epci_stock}') WHERE siren_epci = '241300375'"""
    ).fetchone()
    assert (m1b, m3b) == (1000, 500)
    assert secretb is False


def test_siren_is_varchar_9(epci_stock: Path) -> None:
    con = duckdb_io.connect()
    bad = con.execute(
        f"SELECT count(*) FILTER (WHERE length(siren_epci) <> 9) FROM read_parquet('{epci_stock}')"
    ).fetchone()[0]
    assert bad == 0


def test_periode_secret(tmp_path: Path) -> None:
    out = tmp_path / "epci_stock_periode.parquet"
    staging.build_epci_stock_periode(csv=FIX / "fideli_par_periode_sample.csv", out=out)
    con = duckdb_io.connect()
    n, m3_lt1919 = con.execute(
        f"""SELECT count(*),
                   max(maisons_rga3) FILTER (WHERE periode_construction = '<1919')
            FROM read_parquet('{out}') WHERE siren_epci = '200000172'"""
    ).fetchone()
    assert n == 3
    assert m3_lt1919 is None  # 'secret' → NULL
