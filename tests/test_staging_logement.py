"""Contrats sur le staging INSEE logement (sur fixture CSV offline, format INSEE `;`)."""

from __future__ import annotations

from pathlib import Path

import pytest

from solveille.common import duckdb_io
from solveille.transform import staging

FIXTURE = Path(__file__).parent / "fixtures" / "insee_logement_sample.csv"


@pytest.fixture
def logement_parquet(tmp_path: Path) -> Path:
    out = tmp_path / "commune_logement.parquet"
    staging.build_commune_logement(csv=FIXTURE, out=out)
    return out


def test_schema_and_types(logement_parquet: Path) -> None:
    con = duckdb_io.connect()
    cols = {
        c[0]
        for c in con.execute(
            f"SELECT * FROM read_parquet('{logement_parquet}') LIMIT 0"
        ).description
    }
    assert {"code_insee", "n_maisons", "n_appart", "n_logements"}.issubset(cols)
    n, nnull = con.execute(
        f"SELECT count(*), count(*) FILTER (WHERE code_insee IS NULL) "
        f"FROM read_parquet('{logement_parquet}')"
    ).fetchone()
    assert nnull == 0
    # 5 lignes - 1 arrondissement (75101) exclu = 4
    assert n == 4


def test_plm_arrondissements_excluded(logement_parquet: Path) -> None:
    con = duckdb_io.connect()
    codes = {
        r[0]
        for r in con.execute(
            f"SELECT code_insee FROM read_parquet('{logement_parquet}')"
        ).fetchall()
    }
    assert "75056" in codes  # commune Paris conservée
    assert "75101" not in codes  # arrondissement exclu
    assert "2A004" in codes  # Corse conservée (VARCHAR)


def test_maisons_decimal_parsed(logement_parquet: Path) -> None:
    con = duckdb_io.connect()
    toulouse = con.execute(
        f"SELECT n_maisons FROM read_parquet('{logement_parquet}') WHERE code_insee = '31555'"
    ).fetchone()[0]
    assert toulouse == pytest.approx(48489.89, abs=0.1)
