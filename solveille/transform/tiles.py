"""Génération des PMTiles communaux (choroplèthe de pression IP-RGA) via tippecanoe.

Étape 1 (DuckDB, testable) : joint la géométrie commune (staging) au mart statique +
**pivot temporel** du mart mensuel → un attribut `n_AAAAMM` par mois (niveau IP-RGA 0-5,
0 = pas d'argile / hors couverture), simplifie pour l'affichage (~75 m), reprojette en
**WGS84** (always_xy) et exporte un GeoJSON. Étape 2 : `tippecanoe` → PMTiles **statique
unique** (le curseur de date du front colore par `["get", "n_"+mois]`). Voir ADR-016.
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

# Pivot mensuel → 1 colonne `n_AAAAMM` par mois (niveau IP-RGA 0-5 ; 0 si niveau NULL =
# pas d'argile / hors couverture). Une ligne par (insee, mois) ⇒ MAX = la valeur du mois.
_PIVOT_MONTHS_SQL = (
    "SELECT DISTINCT date_mois::VARCHAR AS d, strftime(date_mois, '%Y%m') AS k "
    "FROM read_parquet('{mensuel}') ORDER BY d"
)

_GEOJSON_SQL = """
COPY (
  WITH piv AS (
    SELECT insee, {pivot_cols}
    FROM read_parquet('{mensuel}')
    GROUP BY insee
  )
  SELECT
    m.insee, m.nom, m.code_dept,
    round(m.E, 4)                          AS E,
    m.classe_dominante,
    m.basculement_2026,
    m.has_rga_coverage,
    round(m.part_alea_moyen_fort, 4)       AS part_alea_moyen_fort,
    round(m.n_maisons_exposees)            AS n_maisons_exposees,
    round(m.valeur_bati_exposee_eur)       AS valeur_bati_exposee_eur,
    m.prix_median_maison_eur_m2,
    m.ip_rga_score,
    m.ip_rga_niveau,
    piv.* EXCLUDE (insee),
    ST_Transform(
      ST_SimplifyPreserveTopology(ST_GeomFromWKB(c.geom_wkb), {simplify}),
      'EPSG:2154', 'EPSG:4326', always_xy := true
    )                                      AS geom
  FROM read_parquet('{mart}') m
  JOIN read_parquet('{commune}') c ON c.code_insee = m.insee
  LEFT JOIN piv ON piv.insee = m.insee
) TO '{out}' WITH (FORMAT GDAL, DRIVER 'GeoJSON', SRS 'EPSG:4326');
"""


def build_geojson(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    commune_parquet: Path | None = None,
    mart_parquet: Path | None = None,
    mensuel_parquet: Path | None = None,
    out: Path | None = None,
    simplify_m: float = SIMPLIFY_M,
) -> Path:
    """Exporte le GeoJSON communal (WGS84) : mart statique + géométrie simplifiée + un
    attribut de niveau IP-RGA `n_AAAAMM` par mois (pivot du mart mensuel)."""
    s = get_settings()
    commune_parquet = commune_parquet or (s.staging_dir / "commune.parquet")
    mart_parquet = mart_parquet or (s.marts_dir / "commune_pression.parquet")
    mensuel_parquet = mensuel_parquet or (s.marts_dir / "commune_pression_mensuel.parquet")
    out = out or (Path("tiles/out") / "communes.geojson")
    out.parent.mkdir(parents=True, exist_ok=True)
    own = con is None
    con = con or duckdb_io.connect()
    try:
        months = con.execute(_PIVOT_MONTHS_SQL.format(mensuel=mensuel_parquet)).fetchall()
        pivot_cols = ", ".join(
            f"MAX(CASE WHEN date_mois = DATE '{d}' THEN COALESCE(ip_rga_niveau_code, 0) END) "
            f"AS n_{k}"
            for d, k in months
        )
        con.execute(
            _GEOJSON_SQL.format(
                simplify=simplify_m,
                mart=mart_parquet,
                commune=commune_parquet,
                mensuel=mensuel_parquet,
                pivot_cols=pivot_cols,
                out=out,
            )
        )
    finally:
        if own:
            con.close()
    log.info("tiles.geojson", path=str(out), bytes=out.stat().st_size, n_mois=len(months))
    return out


def build_tiles(
    *,
    commune_parquet: Path | None = None,
    mart_parquet: Path | None = None,
    mensuel_parquet: Path | None = None,
    out_dir: Path | None = None,
    tippecanoe_bin: str = "tippecanoe",
) -> Path:
    """Construit `tiles/out/communes.pmtiles` (GeoJSON → tippecanoe). Choroplèthe Z3-11."""
    out_dir = out_dir or Path("tiles/out")
    out_dir.mkdir(parents=True, exist_ok=True)
    geojson = build_geojson(
        commune_parquet=commune_parquet,
        mart_parquet=mart_parquet,
        mensuel_parquet=mensuel_parquet,
        out=out_dir / "communes.geojson",
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
