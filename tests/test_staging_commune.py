"""Contrats de données sur le staging `commune` (sur fixture GPKG offline).

Vérifie schéma, clés (`code_insee` non nul, longueur 5), cohérence SRS (centroïdes dans
l'enveloppe Lambert-93 métropole), validité géométrique, présence de l'EPCI, et le
bornage par département.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from solveille.common import duckdb_io, geo
from solveille.transform import staging

FIXTURE = Path(__file__).parent / "fixtures" / "admin_express_sample.gpkg"
EXPECTED_COLS = {
    "code_insee",
    "nom",
    "code_dept",
    "siren_epci",
    "code_siren",
    "population",
    "geom_wkb",
}


@pytest.fixture
def commune_parquet(tmp_path: Path) -> Path:
    """Construit le staging commune (national sur le fixture, 6 communes)."""
    out = tmp_path / "commune.parquet"
    staging.build_commune(gpkg=FIXTURE, out=out, departements=[])
    return out


def test_schema_and_keys(commune_parquet: Path) -> None:
    con = duckdb_io.connect()
    cols = {
        c[0]
        for c in con.execute(f"SELECT * FROM read_parquet('{commune_parquet}') LIMIT 0").description
    }
    assert EXPECTED_COLS.issubset(cols)
    n, n_null, n_badlen = con.execute(
        f"""
        SELECT count(*),
               count(*) FILTER (WHERE code_insee IS NULL),
               count(*) FILTER (WHERE length(code_insee) <> 5)
        FROM read_parquet('{commune_parquet}')
        """
    ).fetchone()
    assert n == 6
    assert n_null == 0
    assert n_badlen == 0


def test_srs_is_lambert93(commune_parquet: Path) -> None:
    con = duckdb_io.connect()
    xmin, ymin, xmax, ymax = geo.METROPOLE_L93_BBOX
    bad = con.execute(
        f"""
        SELECT count(*) FROM (
          SELECT ST_Centroid(ST_GeomFromWKB(geom_wkb)) AS g FROM read_parquet('{commune_parquet}')
        ) WHERE ST_X(g) < {xmin} OR ST_X(g) > {xmax} OR ST_Y(g) < {ymin} OR ST_Y(g) > {ymax}
        """
    ).fetchone()[0]
    assert bad == 0


def test_geometry_valid_and_epci_present(commune_parquet: Path) -> None:
    con = duckdb_io.connect()
    invalid, siren_null = con.execute(
        f"""
        SELECT count(*) FILTER (WHERE NOT ST_IsValid(ST_GeomFromWKB(geom_wkb))),
               count(*) FILTER (WHERE siren_epci IS NULL)
        FROM read_parquet('{commune_parquet}')
        """
    ).fetchone()
    assert invalid == 0
    assert siren_null == 0


def test_departement_bornage(tmp_path: Path) -> None:
    out = tmp_path / "c31.parquet"
    staging.build_commune(gpkg=FIXTURE, out=out, departements=["31"])
    con = duckdb_io.connect()
    deps = [
        r[0]
        for r in con.execute(f"SELECT DISTINCT code_dept FROM read_parquet('{out}')").fetchall()
    ]
    assert deps == ["31"]
