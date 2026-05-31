"""Test de l'export GeoJSON des tuiles : join mart + géométrie, reprojection WGS84,
et pivot temporel des niveaux IP-RGA en attributs `n_AAAAMM` ; **couverture complète** des
tuiles (aucune commune droppée au dézoom — A1) et flags tippecanoe non destructifs."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from solveille.common import duckdb_io
from solveille.transform.tiles import build_geojson, build_tiles, tippecanoe_cmd

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
# Mensuel : 2 mois → niveau n_AAAAMM (3,4) + tension T → bin t_AAAAMM (T=0.5→2, T=0.8→3).
_MENSUEL = """SELECT * FROM (VALUES
    ('31555', DATE '2024-07-01', 3, 0.5), ('31555', DATE '2024-08-01', 4, 0.8)
  ) t(insee, date_mois, ip_rga_niveau_code, T)"""


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
        row = con.execute(
            "SELECT n_202407, n_202408, t_202407, t_202408, e_bin "
            f"FROM ST_Read('{geojson}') LIMIT 1"
        ).fetchone()
        # niveau (3,4), bin tension (T=0.5→2, T=0.8→3), e_bin (E=0.878 ≥ 0.6 → 3).
        assert row == (3, 4, 2, 3, 3)
    finally:
        con.close()


# --- Couverture complète des tuiles (A1 : plus de drop au dézoom) ---------------------------


def test_tippecanoe_cmd_is_non_destructive() -> None:
    """Garde-fou contre la régression A1 : la commande tippecanoe ne doit JAMAIS dropper/fusionner
    de communes au dézoom (`--drop-densest-as-needed` / `--coalesce-smallest-as-needed`), et doit
    lever les limites de taille/feature + ordonner en Hilbert."""
    cmd = " ".join(tippecanoe_cmd(Path("out.pmtiles"), Path("in.geojson")))
    assert "--drop-densest-as-needed" not in cmd  # ← cause du bug A1
    assert "--coalesce" not in cmd  # ni -smallest-as-needed ni --coalesce nu (fusion silencieuse)
    assert "--no-tile-size-limit" in cmd
    assert "--no-feature-limit" in cmd
    assert "--no-tiny-polygon-reduction" in cmd
    assert "--no-simplification-of-shared-nodes" in cmd  # bords partagés cohérents (A2)
    assert "--hilbert" in cmd
    assert "--minimum-zoom=4" in cmd and "--maximum-zoom=9" in cmd


#: Grille 3×2 de communes adjacentes (~1 km² chacune, L93 autour de Toulouse) → 6 INSEE distincts.
#: Adjacentes ⇒ tuiles bas-zoom denses : l'ancien pipeline en aurait droppé au dézoom.
def _grid_communes_sql() -> str:
    cells = []
    for i in range(3):
        for j in range(2):
            x0, y0 = 573000 + i * 1000, 6278000 + j * 1000
            x1, y1 = x0 + 1000, y0 + 1000
            insee = f"3100{i}{j}"
            poly = f"POLYGON(({x0} {y0},{x1} {y0},{x1} {y1},{x0} {y1},{x0} {y0}))"
            cells.append(
                f"SELECT '{insee}' AS code_insee, ST_AsWKB(ST_GeomFromText('{poly}')) AS geom_wkb"
            )
    return " UNION ALL ".join(cells)


def _decode_all_insee(pmtiles: Path) -> set[str]:
    """Décode TOUTES les tuiles (tippecanoe-decode sans z/x/y) et renvoie l'ensemble des INSEE
    présents. La sortie est un FeatureCollection imbriqué (tuile → couche → features)."""
    r = subprocess.run(
        ["tippecanoe-decode", "-l", "communes", str(pmtiles)],
        capture_output=True,
        text=True,
        check=True,
    )
    out: set[str] = set()
    for line in r.stdout.splitlines():
        line = line.strip().rstrip(",")
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        def _collect(node: object) -> None:
            if isinstance(node, dict):
                if node.get("type") == "Feature":
                    insee = node.get("properties", {}).get("insee")
                    if insee:
                        out.add(insee)
                for v in node.values():
                    _collect(v)
            elif isinstance(node, list):
                for v in node:
                    _collect(v)

        _collect(obj)
    return out


@pytest.mark.skipif(
    shutil.which("tippecanoe") is None or shutil.which("tippecanoe-decode") is None,
    reason="tippecanoe/tippecanoe-decode requis pour le test de couverture des tuiles",
)
def test_tiles_full_coverage(tmp_path: Path) -> None:
    """A1 bout-en-bout : 6 communes adjacentes → la pmtiles les contient TOUTES (aucun drop).
    Indépendant de mapshaper (fallback si absent) : la couverture est l'invariant testé."""
    commune = tmp_path / "commune.parquet"
    mart = tmp_path / "mart.parquet"
    mensuel = tmp_path / "mensuel.parquet"
    insees = [f"3100{i}{j}" for i in range(3) for j in range(2)]
    mart_rows = ",\n".join(
        f"('{ins}', 'C{ins}', '31', 0.5, 'Moyen', FALSE, TRUE, 0.5, "
        f"100.0, 1e6, 2000.0, 30, 'Modérée')"
        for ins in insees
    )
    mensuel_rows = ",\n".join(f"('{ins}', DATE '2024-08-01', 3, 0.5)" for ins in insees)
    with duckdb_io.connection() as con:
        con.execute(f"COPY ({_grid_communes_sql()}) TO '{commune}' (FORMAT PARQUET)")
        con.execute(
            f"""COPY (SELECT * FROM (VALUES {mart_rows}) t(insee, nom, code_dept, E,
                classe_dominante, basculement_2026, has_rga_coverage, part_alea_moyen_fort,
                n_maisons_exposees, valeur_bati_exposee_eur, prix_median_maison_eur_m2,
                ip_rga_score, ip_rga_niveau)) TO '{mart}' (FORMAT PARQUET)"""
        )
        con.execute(
            f"""COPY (SELECT * FROM (VALUES {mensuel_rows})
                t(insee, date_mois, ip_rga_niveau_code, T))
                TO '{mensuel}' (FORMAT PARQUET)"""
        )
    pmtiles = build_tiles(
        commune_parquet=commune,
        mart_parquet=mart,
        mensuel_parquet=mensuel,
        out_dir=tmp_path / "out",
    )
    decoded = _decode_all_insee(pmtiles)
    missing = set(insees) - decoded
    assert not missing, f"communes droppées des tuiles (régression A1) : {sorted(missing)}"
