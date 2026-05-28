"""Contrats sur le staging RGA (reprojection 4326→2154 + schéma) sur fixture offline."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from solveille.common import duckdb_io, geo
from solveille.transform import staging

FIXTURE = Path(__file__).parent / "fixtures" / "rga_sample.geojson"


@pytest.fixture
def rga_parquet(tmp_path: Path) -> Path:
    """Construit le staging RGA depuis un GeoJSON fixture (3 features, dept 31)."""
    raw = tmp_path / "raw"
    raw.mkdir()
    shutil.copy(FIXTURE, raw / "31.geojson")
    out = tmp_path / "rga.parquet"
    staging.build_rga(raw_dir=raw, out=out)
    return out


def test_schema_and_domain(rga_parquet: Path) -> None:
    con = duckdb_io.connect()
    cols = {
        c[0]
        for c in con.execute(f"SELECT * FROM read_parquet('{rga_parquet}') LIMIT 0").description
    }
    assert {"code_dept", "niveau", "alea", "geom_wkb"}.issubset(cols)
    n, bad_niveau, bad_alea = con.execute(
        f"""
        SELECT count(*),
               count(*) FILTER (WHERE niveau NOT IN (1, 2, 3)),
               count(*) FILTER (WHERE alea NOT IN ('Faible', 'Moyen', 'Fort'))
        FROM read_parquet('{rga_parquet}')
        """
    ).fetchone()
    assert n == 3
    assert bad_niveau == 0
    assert bad_alea == 0


def test_reprojected_to_lambert93(rga_parquet: Path) -> None:
    """Les coordonnées GeoJSON (lon/lat) doivent ressortir en mètres L93 (bbox métropole)."""
    con = duckdb_io.connect()
    xmin, ymin, xmax, ymax = geo.METROPOLE_L93_BBOX
    bad, invalid = con.execute(
        f"""
        SELECT count(*) FILTER (
                 WHERE ST_X(ST_Centroid(ST_GeomFromWKB(geom_wkb))) NOT BETWEEN {xmin} AND {xmax}
                    OR ST_Y(ST_Centroid(ST_GeomFromWKB(geom_wkb))) NOT BETWEEN {ymin} AND {ymax}),
               count(*) FILTER (WHERE NOT ST_IsValid(ST_GeomFromWKB(geom_wkb)))
        FROM read_parquet('{rga_parquet}')
        """
    ).fetchone()
    assert bad == 0
    assert invalid == 0
