"""Génération des PMTiles communaux (choroplèthe de pression IP-RGA) via tippecanoe.

Étape 1 (DuckDB, testable) : joint la géométrie commune (staging) au mart statique +
**pivot temporel** du mart mensuel → un attribut `n_AAAAMM` par mois (niveau IP-RGA 0-5,
0 = pas d'argile / hors couverture), reprojette en **WGS84** (always_xy) et exporte un GeoJSON.
La géométrie est rendue valide (`ST_MakeValid`) mais **non simplifiée** en DuckDB (cf. étape 2).

Étape 2 (mapshaper, optionnelle) : pré-simplification **topologique** — chaque bord partagé est
un arc unique simplifié une seule fois → **zéro gap/overlap** entre communes adjacentes (corrige
les « slivers » de l'ancienne double simplification non-topologique). Fallback gracieux : si
mapshaper est absent, on saute cette étape et tippecanoe simplifie un cran plus fort.

Étape 3 (tippecanoe) → PMTiles **statique unique** (le curseur de date du front colore par
`["get", "n_"+mois]`). Couverture **complète** (pas de drop au dézoom) : `--no-tile-size-limit`
`--no-feature-limit` + `--no-tiny-polygon-reduction` ; bords cohérents via
`--no-simplification-of-shared-nodes` ; ordre spatial `--hilbert`. Zoom **4→9** (la métropole
tient à ~z5 ; à z9 une commune fait des centaines de px). Voir ADR-016 et docs/05-START-V3 (A1/A2).
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

#: Tolérance de simplification DuckDB (mètres, en L93). **0 = désactivée** : la simplification
#: est faite par mapshaper (topologique) pour ne pas créer de slivers (double simplif → A2).
SIMPLIFY_M = 0.0

#: Taux de simplification Visvalingam de mapshaper (préserve la topologie partagée).
MAPSHAPER_SIMPLIFY = "8%"

#: Zoom min/max des tuiles (z4 = métropole entière ; z9 = commune ~centaines de px). Le front
#: sur-zoome au-delà (overzoom vectoriel) → inutile de monter (z11 = ×4 RAM/taille pour rien).
MIN_ZOOM = 4
MAX_ZOOM = 9

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
      {geom_expr},
      'EPSG:2154', 'EPSG:4326', always_xy := true
    )                                      AS geom
  FROM read_parquet('{mart}') m
  JOIN read_parquet('{commune}') c ON c.code_insee = m.insee
  LEFT JOIN piv ON piv.insee = m.insee
) TO '{out}' WITH (FORMAT GDAL, DRIVER 'GeoJSON', SRS 'EPSG:4326');
"""


def _geom_expr(simplify_m: float) -> str:
    """Expression DuckDB de la géométrie commune (toujours validée ; simplifiée seulement si
    `simplify_m > 0` — par défaut 0, la simplification revient à mapshaper)."""
    valid = "ST_MakeValid(ST_GeomFromWKB(c.geom_wkb))"
    if simplify_m > 0:
        return f"ST_SimplifyPreserveTopology({valid}, {simplify_m})"
    return valid


def build_geojson(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    commune_parquet: Path | None = None,
    mart_parquet: Path | None = None,
    mensuel_parquet: Path | None = None,
    out: Path | None = None,
    simplify_m: float = SIMPLIFY_M,
) -> Path:
    """Exporte le GeoJSON communal (WGS84) : mart statique + géométrie valide (non simplifiée par
    défaut) + un attribut de niveau IP-RGA `n_AAAAMM` par mois (pivot du mart mensuel)."""
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
        if not months:
            # Sans mois, `pivot_cols` serait vide → SQL invalide : on lève un message actionnable.
            raise ValueError(
                f"Mart mensuel vide ({mensuel_parquet}) — lance `make build` avant `make tiles`."
            )
        pivot_cols = ", ".join(
            f"MAX(CASE WHEN date_mois = DATE '{d}' THEN COALESCE(ip_rga_niveau_code, 0) END) "
            f"AS n_{k}"
            for d, k in months
        )
        con.execute(
            _GEOJSON_SQL.format(
                geom_expr=_geom_expr(simplify_m),
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


def _mapshaper_bin() -> str | None:
    """Binaire mapshaper disponible (préférence `mapshaper-xl` qui relève la heap Node pour les
    gros fichiers nationaux), ou None si mapshaper n'est pas installé."""
    for b in ("mapshaper-xl", "mapshaper"):
        if shutil.which(b) is not None:
            return b
    return None


def simplify_topology(
    src: Path,
    out: Path | None = None,
    *,
    simplify: str = MAPSHAPER_SIMPLIFY,
    mapshaper_bin: str | None = None,
) -> Path | None:
    """Simplification **topologique** du GeoJSON via mapshaper (Visvalingam, arcs partagés
    simplifiés une seule fois → zéro gap/overlap). `-clean` répare les overlaps préexistants,
    `keep-shapes` garde les petites communes. Renvoie le chemin de sortie, ou **None** si
    mapshaper est absent (fallback : tippecanoe simplifie alors un cran plus fort)."""
    binary = mapshaper_bin or _mapshaper_bin()
    if binary is None:
        log.warning("tiles.mapshaper_absent", hint="npm i -g mapshaper — fallback tippecanoe")
        return None
    out = out or src.with_name("communes_simplified.geojson")
    cmd = [
        binary,
        "-i",
        str(src),
        "-clean",
        "gap-fill-area=0",
        "-simplify",
        simplify,
        "keep-shapes",
        "-o",
        str(out),
        "precision=0.000001",
        "format=geojson",
    ]
    log.info("tiles.mapshaper", cmd=" ".join(cmd))
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    log.info("tiles.mapshaper.done", path=str(out), bytes=out.stat().st_size)
    return out


def tippecanoe_cmd(
    pmtiles: Path,
    geojson: Path,
    *,
    tippecanoe_bin: str = "tippecanoe",
    simplification: int = 1,
    min_zoom: int = MIN_ZOOM,
    max_zoom: int = MAX_ZOOM,
) -> list[str]:
    """Commande tippecanoe (testable). **Couverture complète** : pas de drop au dézoom
    (`--no-tile-size-limit --no-feature-limit`), pas de réduction des micro-polygones
    (`--no-tiny-polygon-reduction`), bords partagés cohérents (`-pn`), ordre spatial Hilbert.
    La géométrie est déjà simplifiée par mapshaper → `--simplification=1`."""
    return [
        tippecanoe_bin,
        "-o",
        str(pmtiles),
        "-l",
        "communes",
        f"--minimum-zoom={min_zoom}",
        f"--maximum-zoom={max_zoom}",
        "--no-simplification-of-shared-nodes",
        "--no-tiny-polygon-reduction",
        # PAS de --coalesce : chaque commune est 1 feature à attributs uniques (insee) → la fusion
        # n'aurait aucun effet utile, et serait un risque silencieux si un jour on réduisait les
        # attributs émis par tuile (2 voisines deviendraient identiques → fusionnées). Cf. review A.
        "--no-tile-size-limit",
        "--no-feature-limit",
        "--hilbert",
        f"--simplification={simplification}",
        "--force",
        str(geojson),
    ]


def build_tiles(
    *,
    commune_parquet: Path | None = None,
    mart_parquet: Path | None = None,
    mensuel_parquet: Path | None = None,
    out_dir: Path | None = None,
    tippecanoe_bin: str = "tippecanoe",
    use_mapshaper: bool = True,
    keep_intermediates: bool = False,
) -> Path:
    """Construit `tiles/out/communes.pmtiles` (GeoJSON → mapshaper topo → tippecanoe).

    Choroplèthe Z4-9, **couverture complète** (A1) et **sans slivers** (A2, via mapshaper). Si
    mapshaper est absent, fallback : on garde le GeoJSON brut et tippecanoe simplifie plus fort.

    Les GeoJSON intermédiaires (national ~377 Mo non simplifié + le simplifié) sont **supprimés**
    après le build (`keep_intermediates=True` pour les garder en debug) : `tiles/out` est monté tel
    quel par l'API (StaticFiles) → on ne laisse QUE le `.pmtiles` (pas de dump GeoJSON public ni de
    disque qui gonfle à chaque refresh — voir review A / contrainte egress 20 To).
    """
    out_dir = out_dir or Path("tiles/out")
    out_dir.mkdir(parents=True, exist_ok=True)
    if shutil.which(tippecanoe_bin) is None:
        raise FileNotFoundError(f"{tippecanoe_bin} introuvable — installe tippecanoe (brew/apt).")
    geojson = build_geojson(
        commune_parquet=commune_parquet,
        mart_parquet=mart_parquet,
        mensuel_parquet=mensuel_parquet,
        out=out_dir / "communes.geojson",
    )
    # Étape 2 : simplification topologique (mapshaper). Fallback gracieux si absent.
    simplified = simplify_topology(geojson) if use_mapshaper else None
    if simplified is not None:
        tile_input, simplification = simplified, 1
    else:
        tile_input, simplification = geojson, 4  # fallback : tippecanoe simplifie un cran plus fort

    pmtiles = out_dir / "communes.pmtiles"
    cmd = tippecanoe_cmd(
        pmtiles, tile_input, tippecanoe_bin=tippecanoe_bin, simplification=simplification
    )
    log.info("tiles.tippecanoe", cmd=" ".join(cmd), mapshaper=simplified is not None)
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    log.info("tiles.pmtiles", path=str(pmtiles), bytes=pmtiles.stat().st_size)
    if not keep_intermediates:
        for f in (geojson, simplified):
            if f is not None and f != pmtiles:
                f.unlink(missing_ok=True)  # ne garder QUE le .pmtiles dans tiles/out (servi public)
    return pmtiles


def main() -> None:
    build_tiles()


if __name__ == "__main__":
    main()
