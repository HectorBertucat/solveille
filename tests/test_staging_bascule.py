"""Contrats sur le staging « communes basculées 2026 » (sur fixture CSV offline)."""

from __future__ import annotations

from pathlib import Path

import pytest

from solveille.common import duckdb_io
from solveille.transform import staging

FIXTURE = Path(__file__).parent / "fixtures" / "communes_bascule_sample.csv"
_VALID_BASCULE = ("faible_vers_moyenne", "faible_vers_forte", "moyenne_vers_forte")


@pytest.fixture
def bascule_parquet(tmp_path: Path) -> Path:
    out = tmp_path / "commune_bascule.parquet"
    staging.build_commune_bascule(csv=FIXTURE, out=out)
    return out


def test_schema_flag_and_enum(bascule_parquet: Path) -> None:
    con = duckdb_io.connect()
    cols = {
        c[0]
        for c in con.execute(f"SELECT * FROM read_parquet('{bascule_parquet}') LIMIT 0").description
    }
    assert {
        "code_insee",
        "rga_classe_2020",
        "rga_classe_2026",
        "bascule_type",
        "basculement_2026",
    }.issubset(cols)
    n, not_flagged, bad_enum, classe_up = con.execute(
        f"""
        SELECT count(*),
               count(*) FILTER (WHERE basculement_2026 IS NOT TRUE),
               count(*) FILTER (WHERE bascule_type NOT IN {_VALID_BASCULE}),
               count(*) FILTER (WHERE rga_classe_2026 <= rga_classe_2020)
        FROM read_parquet('{bascule_parquet}')
        """
    ).fetchone()
    assert n == 3
    assert not_flagged == 0  # le fichier ne liste que des communes qui basculent
    assert bad_enum == 0
    assert classe_up == 0  # un basculement va vers une classe supérieure


def test_code_insee_preserved(bascule_parquet: Path) -> None:
    """Zéros de tête et Corse (2A) conservés (VARCHAR)."""
    con = duckdb_io.connect()
    codes = {
        r[0]
        for r in con.execute(f"SELECT code_insee FROM read_parquet('{bascule_parquet}')").fetchall()
    }
    assert "01053" in codes
    assert "2A004" in codes
