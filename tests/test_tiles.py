"""Test de l'export GeoJSON des tuiles : join mart + géométrie, reprojection WGS84,
et pivot temporel des niveaux IP-RGA en attributs `n_AAAAMM`."""

from __future__ import annotations

from pathlib import Path

import pytest

from solveille.common import duckdb_io
from solveille.transform.tiles import build_geojson

# Carré ~1 km² en Lambert 93 autour de Toulouse (centroïde attendu ~lon 1.43, lat 43.6).
_COMMUNE = """SELECT '31555' AS code_insee,
  ST_AsWKB(ST_GeomFromText(
    'POLYGON((573000 6278000,574000 6278000,574000 6279000,573000 6279000,573000 6278000))'
  )) AS geom_wkb"""
_MART = """SELECT '31555' AS insee, 'Toulouse' AS nom, '31' AS code_dept,
  0.878 AS E, 'Moyen' AS classe_dominante, TRUE AS basculement_2026, TRUE AS has_rga_coverage,
  0.994 AS part_alea_moyen_fort, 21578.0 AS n_maisons_exposees,
  7.6e9 AS valeur_bati_exposee_eur, 3760.0 AS prix_median_maison_eur_m2,
  45 AS ip_rga_score, 'Élevée' AS ip_rga_niveau"""
# Mensuel : 2 mois → 2 attributs de niveau (n_202407=3, n_202408=4).
_MENSUEL = """SELECT * FROM (VALUES
    ('31555', DATE '2024-07-01', 3), ('31555', DATE '2024-08-01', 4)
  ) t(insee, date_mois, ip_rga_niveau_code)"""


@pytest.fixture
def geojson(tmp_path: Path) -> Path:
    commune = tmp_path / "commune.parquet"
    mart = tmp_path / "mart.parquet"
    mensuel = tmp_path / "mensuel.parquet"
    with duckdb_io.connection() as con:
        con.execute(f"COPY ({_COMMUNE}) TO '{commune}' (FORMAT PARQUET)")
        con.execute(f"COPY ({_MART}) TO '{mart}' (FORMAT PARQUET)")
        con.execute(f"COPY ({_MENSUEL}) TO '{mensuel}' (FORMAT PARQUET)")
    out = tmp_path / "communes.geojson"
    build_geojson(commune_parquet=commune, mart_parquet=mart, mensuel_parquet=mensuel, out=out)
    return out


def test_geojson_properties_and_wgs84(geojson: Path) -> None:
    con = duckdb_io.connect()
    n, insee, e, basc, lon, lat = con.execute(
        f"""SELECT count(*) OVER (), insee, E, basculement_2026,
                   round(ST_X(ST_Centroid(geom)), 2), round(ST_Y(ST_Centroid(geom)), 2)
            FROM ST_Read('{geojson}') LIMIT 1"""
    ).fetchone()
    assert n == 1
    assert insee == "31555"
    assert e == pytest.approx(0.878)
    assert basc is True
    # reprojection L93 → WGS84 : Toulouse ≈ (1.43, 43.6)
    assert 1.3 < lon < 1.5
    assert 43.5 < lat < 43.7


def test_geojson_temporal_niveau_attributes(geojson: Path) -> None:
    con = duckdb_io.connect()
    try:
        row = con.execute(f"SELECT n_202407, n_202408 FROM ST_Read('{geojson}') LIMIT 1").fetchone()
    finally:
        con.close()
    assert row == (3, 4)  # un attribut de niveau IP-RGA par mois (pivot)
