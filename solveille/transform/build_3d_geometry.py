"""Précompute de la 3D animée GPU (deck.gl) — « montagnes de pression ».

Produit un artefact binaire compact que le front charge **une fois** pour rendre les communes
en extrusion GPU (`deck.gl SolidPolygonLayer` binaire), avec une **élévation continue** dérivée
du score `ip_rga_score` (0-100) et une couleur de niveau, qui **morphent entre les mois** pendant
l'animation. Indépendant des PMTiles (qui restent le rendu 2D, plus rapide) : deck n'agit qu'en 3D.

Sorties (servies `/tiles/…` par StaticFiles, **gitignorées**, régénérées comme les PMTiles) :
- `communes-3d.json` : header (compteurs, mois, ordre `insee`, seuils, paramètres d'élévation,
  `layout` = offsets/dtypes des sections du `.bin`).
- `communes-3d.bin` : sections little-endian concaténées —
  `positions` Float32 (lng,lat WGS84) | `partStarts` Uint32 (index de sommet de début par **partie**
  de polygone, + fermeture = `data.startIndices` deck) | `partCommune` Uint32 (index commune par
  partie → l'accesseur lit `scores[commune*nMois + mois]`) | `scores` Uint8 `[commune][mois]`.

Géométrie : réutilise le patron de `tiles.py` (`ST_MakeValid` → `ST_SimplifyPreserveTopology` →
`ST_Transform` WGS84 `always_xy`), simplifiée plus fort (150 m) car le national 3D n'a pas besoin de
la perfection topologique du choroplèthe 2D (qui reste autoritaire). **MVP : anneaux extérieurs
seulement** (trous ignorés — 29 communes concernées, invisibles à l'échelle nationale ; supprime le
besoin du masque `vertexValid`/winding). MultiPolygon → une partie par polygone.

⚠️ numpy est absent du projet → on assemble avec le module `array` de la stdlib.
"""

from __future__ import annotations

import json
import sys
from array import array
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from solveille.common import duckdb_io
from solveille.common.config import get_settings
from solveille.common.logging import get_logger

log = get_logger("solveille.transform.build_3d_geometry")

#: Tolérance de simplification (mètres, en L93) dédiée à la 3D — plus agressive que le 2D pour
#: limiter le nombre de sommets envoyés au GPU (le choroplèthe 2D reste autoritaire pour les bords).
SIMPLIFY_M_3D = 150.0

#: Hauteur (m) du score maximal — calée sur l'ancienne marche niveau 5 (`NIVEAU_HEIGHT[5]`) pour
#: conserver le « ressenti » de relief, mais l'élévation devient **continue** : `(score/max)^γ·H`.
MAX_HEIGHT_M = 40000.0

_PIVOT_MONTHS_SQL = (
    "SELECT DISTINCT date_mois::VARCHAR AS d, strftime(date_mois, '%Y%m') AS k "
    "FROM read_parquet('{mensuel}') ORDER BY d"
)


def _ensure_little_endian(arr: array[Any]) -> bytes:
    """Octets little-endian d'un `array` (byteswap si la machine est big-endian — jamais sur nos
    cibles x86/arm LE, mais on reste portable et reproductible)."""
    if sys.byteorder == "big":
        arr = array(arr.typecode, arr)
        arr.byteswap()
    return arr.tobytes()


def extract_rings(geom: dict[str, Any]) -> list[list[tuple[float, float]]]:
    """Anneaux EXTÉRIEURS d'une géométrie GeoJSON (Polygon/MultiPolygon) — trous ignorés (MVP).

    Renvoie une liste d'anneaux ; chaque anneau = liste de (lng, lat), fermé (1ᵉʳ == dernier sommet,
    conservé comme l'attend deck). Un Polygon → 1 anneau ; un MultiPolygon → 1 anneau par polygone.
    """
    t = geom.get("type")
    coords = geom.get("coordinates") or []
    rings: list[list[tuple[float, float]]] = []
    if t == "Polygon":
        if coords:
            rings.append([(float(x), float(y)) for x, y in coords[0]])
    elif t == "MultiPolygon":
        for poly in coords:
            if poly:
                rings.append([(float(x), float(y)) for x, y in poly[0]])
    return rings


def assemble(
    communes: Iterable[tuple[str, list[list[tuple[float, float]]], list[int], int]],
    months: list[str],
    *,
    max_score: int,
    gamma: float,
    seuils: list[int],
) -> tuple[bytes, dict[str, Any]]:
    """Assemble le `.bin` + le header depuis un itérable `(insee, anneaux, scores[nMois], hasClay)`.

    `hasClay` (0/1) distingue les communes hors couverture argile (E=0) : le front les laisse
    **grises et plates** (comme la 2D), au lieu de binner leur score 0 en niveau 1. Fonction
    **pure** (sans I/O ni DuckDB) → testable sur des données synthétiques.
    """
    positions = array("f")  # Float32 lng,lat aplatis
    part_starts = array("I")  # Uint32 : index de sommet de début par partie (+ fermeture)
    part_commune = array("I")  # Uint32 : index commune de chaque partie
    scores_flat = array("B")  # Uint8 [commune*nMois + mois]
    has_clay = array("B")  # Uint8 [commune] : 1 si E>0 (sinon gris/plat comme la 2D)
    assert positions.itemsize == 4 and part_starts.itemsize == 4
    n_months = len(months)
    insee_order: list[str] = []
    vtx = 0  # compteur de sommets cumulé
    minx = miny = float("inf")
    maxx = maxy = float("-inf")

    for insee, rings, scores, clay in communes:
        if not rings:
            continue  # commune sans géométrie exploitable → ignorée (alignement scores/insee)
        ci = len(insee_order)
        for ring in rings:
            part_starts.append(vtx)
            part_commune.append(ci)
            for lng, lat in ring:
                positions.append(lng)
                positions.append(lat)
                if lng < minx:
                    minx = lng
                if lng > maxx:
                    maxx = lng
                if lat < miny:
                    miny = lat
                if lat > maxy:
                    maxy = lat
                vtx += 1
        # Une ligne de scores par commune (Uint8 borné 0-255 ; les scores réels sont 0-100).
        row = (scores + [0] * n_months)[:n_months]
        scores_flat.extend(max(0, min(255, int(s or 0))) for s in row)
        has_clay.append(1 if clay else 0)
        insee_order.append(insee)
    part_starts.append(vtx)  # fermeture : fin de la dernière partie

    pos_b = _ensure_little_endian(positions)
    ps_b = _ensure_little_endian(part_starts)
    pc_b = _ensure_little_endian(part_commune)
    sc_b = _ensure_little_endian(scores_flat)
    hc_b = _ensure_little_endian(has_clay)
    off_ps = len(pos_b)
    off_pc = off_ps + len(ps_b)
    off_sc = off_pc + len(pc_b)
    off_hc = off_sc + len(sc_b)
    header = {
        "version": 1,
        "nCommunes": len(insee_order),
        "nMonths": n_months,
        "nVertices": len(positions) // 2,
        "nParts": len(part_commune),
        "months": months,
        "insee": insee_order,
        "seuils": seuils,
        "elevation": {"maxScore": int(max_score), "maxHeightM": MAX_HEIGHT_M, "gamma": gamma},
        "bbox": ([minx, miny, maxx, maxy] if insee_order else [0.0, 0.0, 0.0, 0.0]),
        "layout": {
            "positions": {"offset": 0, "dtype": "Float32", "count": len(positions)},
            "partStarts": {"offset": off_ps, "dtype": "Uint32", "count": len(part_starts)},
            "partCommune": {"offset": off_pc, "dtype": "Uint32", "count": len(part_commune)},
            "scores": {"offset": off_sc, "dtype": "Uint8", "count": len(scores_flat)},
            "hasClay": {"offset": off_hc, "dtype": "Uint8", "count": len(has_clay)},
        },
    }
    return pos_b + ps_b + pc_b + sc_b + hc_b, header


def _read_seuils(marts_dir: Path) -> tuple[list[int], float]:
    """Seuils de niveau + gamma depuis `marts/seuils_niveaux.json` (repli sur des défauts sûrs)."""
    p = marts_dir / "seuils_niveaux.json"
    try:
        d = json.loads(p.read_text())
        return [int(x) for x in d.get("seuils", [25, 35, 47, 61])], float(d.get("gamma", 0.8))
    except (OSError, ValueError):
        return [25, 35, 47, 61], 0.8


def _iter_communes(
    con: Any, commune_parquet: Path, mensuel_parquet: Path, months: list[tuple[str, str]]
) -> Iterator[tuple[str, list[list[tuple[float, float]]], list[int], int]]:
    """Itère `(insee, anneaux, scores[nMois], hasClay)` en ordre `insee` (géométrie WGS84 simplifiée
    + pivot des scores), depuis les communes présentes dans le mart mensuel ET le staging commune.

    `hasClay = MAX(ip_rga_niveau_code) > 0` : pour E>0 le niveau est toujours 1-5, pour E=0 il est
    0/NULL → distingue exactement la couverture argile (cohérent avec le gris de la 2D)."""
    # Scores pivotés : une ligne par commune, une colonne de score par mois (ordre des mois figé) +
    # le niveau max (→ hasClay). MAX ignore les NULL ⇒ la valeur du mois (1 ligne par insee/mois).
    score_cols = ", ".join(
        f"MAX(CASE WHEN date_mois = DATE '{d}' THEN COALESCE(ip_rga_score, 0) END) AS s_{k}"
        for d, k in months
    )
    rows = con.execute(
        f"SELECT insee, MAX(COALESCE(ip_rga_niveau_code, 0)) AS maxn, {score_cols} "
        f"FROM read_parquet('{mensuel_parquet}') GROUP BY insee ORDER BY insee"
    ).fetchall()
    # r = (insee, maxn, s_0, …, s_{n-1}) → scores = colonnes après maxn ; hasClay = maxn > 0.
    scores_by_insee = {r[0]: [int(v or 0) for v in r[2:]] for r in rows}
    has_clay_by_insee = {r[0]: 1 if (r[1] or 0) > 0 else 0 for r in rows}

    # Géométrie WGS84 simplifiée, en ordre `insee` aligné sur les scores.
    geom_sql = f"""
      SELECT m.insee,
             ST_AsGeoJSON(
               ST_Transform(
                 ST_SimplifyPreserveTopology(
                   ST_MakeValid(ST_GeomFromWKB(c.geom_wkb)), {SIMPLIFY_M_3D}
                 ),
                 'EPSG:2154', 'EPSG:4326', always_xy := true
               )
             ) AS gj
      FROM (SELECT DISTINCT insee FROM read_parquet('{mensuel_parquet}')) m
      JOIN read_parquet('{commune_parquet}') c ON c.code_insee = m.insee
      ORDER BY m.insee
    """
    for insee, gj in con.execute(geom_sql).fetchall():
        scores = scores_by_insee.get(insee)
        if gj is None or scores is None:
            continue
        rings = extract_rings(json.loads(gj))
        if rings:
            yield insee, rings, scores, has_clay_by_insee.get(insee, 0)


def build_3d_geometry(
    con: Any = None,
    *,
    commune_parquet: Path | None = None,
    mensuel_parquet: Path | None = None,
    out_bin: Path | None = None,
    out_json: Path | None = None,
) -> Path:
    """Construit `tiles/out/communes-3d.{bin,json}` (géométrie WGS84 simplifiée + scores).

    Retourne le chemin du `.bin`. Réutilise le patron géométrique de `tiles.py`.
    """
    s = get_settings()
    commune_parquet = commune_parquet or (s.staging_dir / "commune.parquet")
    mensuel_parquet = mensuel_parquet or (s.marts_dir / "commune_pression_mensuel.parquet")
    out_bin = out_bin or (Path("tiles/out") / "communes-3d.bin")
    out_json = out_json or (Path("tiles/out") / "communes-3d.json")
    out_bin.parent.mkdir(parents=True, exist_ok=True)

    own = con is None
    con = con or duckdb_io.connect()
    try:
        months = con.execute(_PIVOT_MONTHS_SQL.format(mensuel=mensuel_parquet)).fetchall()
        if not months:
            raise ValueError(
                f"Mart mensuel vide ({mensuel_parquet}) — lance `make build` avant `make 3d-data`."
            )
        max_score = int(
            duckdb_io.scalar(
                con, f"SELECT max(ip_rga_score) FROM read_parquet('{mensuel_parquet}')"
            )
            or 100
        )
        seuils, gamma = _read_seuils(s.marts_dir)
        month_keys = [k for _, k in months]
        blob, header = assemble(
            _iter_communes(con, commune_parquet, mensuel_parquet, months),
            month_keys,
            max_score=max_score,
            gamma=gamma,
            seuils=seuils,
        )
    finally:
        if own:
            con.close()

    out_bin.write_bytes(blob)
    out_json.write_text(json.dumps(header, separators=(",", ":")))
    log.info(
        "build_3d_geometry.done",
        bin=str(out_bin),
        bytes=len(blob),
        n_communes=header["nCommunes"],
        n_parts=header["nParts"],
        n_vertices=header["nVertices"],
        n_months=header["nMonths"],
        max_score=max_score,
    )
    return out_bin


def main() -> None:
    build_3d_geometry()


if __name__ == "__main__":
    main()
