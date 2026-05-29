"""Génération des PMTiles communaux (choroplèthe de l'enjeu) via tippecanoe.

Étape 1 (DuckDB, testable) : joint la géométrie commune (staging) au mart, simplifie pour
l'affichage (~75 m), reprojette en **WGS84** (always_xy) et exporte un GeoJSON avec les
attributs servis (E, classe, valeur, flag bascule). Étape 2 : `tippecanoe` → PMTiles statique.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import duckdb

from solveille.common import duckdb_io
from solveille.common.config import get_settings
from solveille.common.logging import get_logger

log = get_logger("solveille.transform.tiles")

#: Tolérance de simplification pour l'affichage (mètres, en L93 avant reprojection).
SIMPLIFY_M = 75.0

_GEOJSON_SQL = """
COPY (
  SELECT
    m.insee, m.nom, m.code_dept,
    round(m.E, 4)                          AS E,
    m.classe_dominante,
    m.basculement_2026,
    round(m.part_alea_moyen_fort, 4)       AS part_alea_moyen_fort,
    round(m.n_maisons_exposees)            AS n_maisons_exposees,
    round(m.valeur_bati_exposee_eur)       AS valeur_bati_exposee_eur,
    m.prix_median_maison_eur_m2,
    ST_Transform(
      ST_SimplifyPreserveTopology(ST_GeomFromWKB(c.geom_wkb), {simplify}),
      'EPSG:2154', 'EPSG:4326', always_xy := true
    )                                      AS geom
  FROM read_parquet('{mart}') m
  JOIN read_parquet('{commune}') c ON c.code_insee = m.insee
) TO '{out}' WITH (FORMAT GDAL, DRIVER 'GeoJSON', SRS 'EPSG:4326');
"""


def build_geojson(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    commune_parquet: Path | None = None,
    mart_parquet: Path | None = None,
    out: Path | None = None,
    simplify_m: float = SIMPLIFY_M,
) -> Path:
    """Exporte le GeoJSON communal (WGS84) joignant mart + géométrie simplifiée."""
    s = get_settings()
    commune_parquet = commune_parquet or (s.staging_dir / "commune.parquet")
    mart_parquet = mart_parquet or (s.marts_dir / "commune_pression.parquet")
    out = out or (Path("tiles/out") / "communes.geojson")
    out.parent.mkdir(parents=True, exist_ok=True)
    own = con is None
    con = con or duckdb_io.connect()
    try:
        con.execute(
            _GEOJSON_SQL.format(
                simplify=simplify_m, mart=mart_parquet, commune=commune_parquet, out=out
            )
        )
    finally:
        if own:
            con.close()
    log.info("tiles.geojson", path=str(out), bytes=out.stat().st_size)
    return out


def build_tiles(
    *,
    commune_parquet: Path | None = None,
    mart_parquet: Path | None = None,
    out_dir: Path | None = None,
    tippecanoe_bin: str = "tippecanoe",
) -> Path:
    """Construit `tiles/out/communes.pmtiles` (GeoJSON → tippecanoe). Choroplèthe Z3-11."""
    out_dir = out_dir or Path("tiles/out")
    out_dir.mkdir(parents=True, exist_ok=True)
    geojson = build_geojson(
        commune_parquet=commune_parquet, mart_parquet=mart_parquet, out=out_dir / "communes.geojson"
    )
    pmtiles = out_dir / "communes.pmtiles"
    if shutil.which(tippecanoe_bin) is None:
        raise FileNotFoundError(f"{tippecanoe_bin} introuvable — installe tippecanoe (brew/apt).")
    cmd = [
        tippecanoe_bin,
        "-o",
        str(pmtiles),
        "-l",
        "communes",
        "--minimum-zoom=3",
        "--maximum-zoom=11",
        "--drop-densest-as-needed",
        "--coalesce-smallest-as-needed",
        "--simplification=10",
        "--force",
        str(geojson),
    ]
    log.info("tiles.tippecanoe", cmd=" ".join(cmd))
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    log.info("tiles.pmtiles", path=str(pmtiles), bytes=pmtiles.stat().st_size)
    return pmtiles


def main() -> None:
    build_tiles()


if __name__ == "__main__":
    main()
