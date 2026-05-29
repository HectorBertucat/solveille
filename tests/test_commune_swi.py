"""Tests du rattachement maille↔commune et du z_SWI communal pondéré par aire.

Géométries synthétiques en EPSG:2154 (planaire, mètres). Deux mailles 8 km côte à côte
(carrés [0,8000]² et [8000,16000]×[0,8000]) :
  - commune A entièrement dans la maille 1 → z = z(maille1), 1 maille ;
  - commune B à cheval 50/50 → z = moyenne pondérée des deux ;
  - commune C (île lointaine, aucun carré ne la couvre) → **repli** sur la maille la plus proche.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from solveille.common import duckdb_io
from solveille.transform import commune_swi

_COMMUNES = [
    # (insee, dept, WKT) ; aires : A=4e6, B=8e6 (2e6 dans maille1 + ... à cheval), C=1e6
    ("99001", "99", "POLYGON((1000 1000, 3000 1000, 3000 3000, 1000 3000, 1000 1000))"),
    ("99002", "99", "POLYGON((6000 1000, 10000 1000, 10000 3000, 6000 3000, 6000 1000))"),
    ("99003", "99", "POLYGON((50000 50000, 51000 50000, 51000 51000, 50000 51000, 50000 50000))"),
]
_GRILLE = [(1, 4000.0, 4000.0), (2, 12000.0, 4000.0)]  # centroïdes → carrés 8 km adjacents
_ANOM = [(1, "2020-01-01", 0.1, -2.0), (2, "2020-01-01", 0.9, 2.0)]  # maille1 sec, maille2 humide


@pytest.fixture
def staged(tmp_path: Path) -> dict[str, Path]:
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
        gvals = ", ".join(f"({m}, {x}, {y})" for m, x, y in _GRILLE)
        grille = tmp_path / "grille.parquet"
        con.execute(
            f"COPY (SELECT * FROM (VALUES {gvals}) AS t(num_maille, x93, y93)) "
            f"TO '{grille}' (FORMAT PARQUET);"
        )
        avals = ", ".join(f"({m}, DATE '{d}', {s}, {z})" for m, d, s, z in _ANOM)
        anom = tmp_path / "anom.parquet"
        con.execute(
            f"COPY (SELECT * FROM (VALUES {avals}) AS t(num_maille, date_mois, swi, z_swi)) "
            f"TO '{anom}' (FORMAT PARQUET);"
        )
    finally:
        con.close()
    return {"commune": commune, "grille": grille, "anom": anom}


def test_poids_intersection_and_fallback(staged: dict[str, Path], tmp_path: Path) -> None:
    con = duckdb_io.connect()
    try:
        poids = commune_swi.build_commune_maille_poids(
            con,
            commune_parquet=staged["commune"],
            grille_parquet=staged["grille"],
            out=tmp_path / "poids.parquet",
        )
        rows = con.execute(
            f"SELECT code_insee, num_maille, round(poids_aire) "
            f"FROM read_parquet('{poids}') ORDER BY code_insee, num_maille"
        ).fetchall()
        n_communes = con.execute(
            f"SELECT count(DISTINCT code_insee) FROM read_parquet('{poids}')"
        ).fetchone()[0]
    finally:
        con.close()
    # A dans maille1 (4e6) ; B à cheval (maille1+maille2) ; C île → repli maille2 (plus proche)
    assert ("99001", 1, 4000000.0) in rows
    assert {(m) for (i, m, _) in rows if i == "99002"} == {1, 2}  # à cheval sur 2 mailles
    assert [(i, m) for (i, m, _) in rows if i == "99003"] == [("99003", 2)]  # repli plus proche
    assert n_communes == 3  # couverture 100 %


def test_commune_swi_weighted_average(staged: dict[str, Path], tmp_path: Path) -> None:
    con = duckdb_io.connect()
    try:
        poids = commune_swi.build_commune_maille_poids(
            con,
            commune_parquet=staged["commune"],
            grille_parquet=staged["grille"],
            out=tmp_path / "poids.parquet",
        )
        out = commune_swi.build_commune_swi(
            con,
            poids_parquet=poids,
            anomalie_parquet=staged["anom"],
            out=tmp_path / "cswi.parquet",
        )
        res = {
            i: (round(z, 4), n)
            for i, z, n in con.execute(
                f"SELECT code_insee, z_swi, n_mailles FROM read_parquet('{out}')"
            ).fetchall()
        }
    finally:
        con.close()
    assert res["99001"] == (-2.0, 1)  # une seule maille (sèche)
    assert res["99002"] == (0.0, 2)  # 50/50 entre -2 et +2 → 0
    assert res["99003"] == (2.0, 1)  # repli sur maille2 (humide)
