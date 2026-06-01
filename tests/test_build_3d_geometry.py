"""Contrat de l'artefact 3D (deck.gl) — vérifié SANS DuckDB ni données (fonctions pures).

On teste `extract_rings` (Polygon/MultiPolygon → anneaux extérieurs, trous ignorés) et `assemble`
(invariants du format binaire `SolidPolygonLayer` : `startIndices` par partie monotones et fermés,
`partCommune` aligné, matrice de scores aux bonnes dimensions, round-trip des coordonnées)."""

from __future__ import annotations

import json
from array import array

from solveille.transform.build_3d_geometry import assemble, extract_rings

# Carrés fermés (1ᵉʳ == dernier sommet, comme GeoJSON / ce qu'attend deck).
_SQUARE_A = [[0.0, 0.0], [0.0, 1.0], [1.0, 1.0], [1.0, 0.0], [0.0, 0.0]]
_SQUARE_B = [[2.0, 2.0], [2.0, 3.0], [3.0, 3.0], [3.0, 2.0], [2.0, 2.0]]
_HOLE = [[0.2, 0.2], [0.2, 0.8], [0.8, 0.8], [0.8, 0.2], [0.2, 0.2]]


def test_extract_rings_polygon_drops_holes() -> None:
    geom = {"type": "Polygon", "coordinates": [_SQUARE_A, _HOLE]}
    rings = extract_rings(geom)
    assert len(rings) == 1  # anneau extérieur seulement (trou ignoré au MVP)
    assert rings[0][0] == (0.0, 0.0)
    assert len(rings[0]) == 5  # sommet de fermeture conservé


def test_extract_rings_multipolygon_one_ring_per_polygon() -> None:
    geom = {"type": "MultiPolygon", "coordinates": [[_SQUARE_A, _HOLE], [_SQUARE_B]]}
    rings = extract_rings(geom)
    assert len(rings) == 2
    assert rings[1][0] == (2.0, 2.0)


def test_extract_rings_empty() -> None:
    assert extract_rings({"type": "Polygon", "coordinates": []}) == []
    assert extract_rings({"type": "Point", "coordinates": [0, 0]}) == []


def _views(blob: bytes, header: dict) -> dict[str, array]:
    """Reconstruit les vues typées depuis le blob selon le `layout` du header (comme le front)."""
    lay = header["layout"]
    out: dict[str, array] = {}
    for name, tc in (
        ("positions", "f"),
        ("partStarts", "I"),
        ("partCommune", "I"),
        ("scores", "B"),
        ("hasClay", "B"),
    ):
        meta = lay[name]
        a = array(tc)
        a.frombytes(blob[meta["offset"] : meta["offset"] + meta["count"] * a.itemsize])
        out[name] = a
    return out


def test_assemble_invariants_and_roundtrip() -> None:
    months = ["202401", "202402", "202403"]
    communes = [
        ("01001", [[(float(x), float(y)) for x, y in _SQUARE_A]], [10, 20, 94], 1),
        # MultiPolygon → 2 parties, même commune ; hasClay=0 (hors couverture argile)
        (
            "01002",
            [
                [(float(x), float(y)) for x, y in _SQUARE_A],
                [(float(x), float(y)) for x, y in _SQUARE_B],
            ],
            [0, 5, 0],
            0,
        ),
        ("01003", [], [1, 2, 3], 1),  # sans géométrie → ignorée
    ]
    blob, header = assemble(communes, months, max_score=94, gamma=0.8, seuils=[25, 35, 47, 61])

    assert header["nCommunes"] == 2  # 01003 ignorée
    assert header["nMonths"] == 3
    assert header["nParts"] == 3  # 1 (01001) + 2 (01002)
    assert header["insee"] == ["01001", "01002"]
    assert header["nVertices"] == 15  # 5 + 5 + 5

    v = _views(blob, header)
    # partStarts : monotone, longueur nParts+1, fermeture = nVertices.
    ps = list(v["partStarts"])
    assert ps == [0, 5, 10, 15]
    assert all(ps[i] < ps[i + 1] for i in range(len(ps) - 1))
    # partCommune : 1 entrée par partie, index commune aligné.
    assert list(v["partCommune"]) == [0, 1, 1]
    # scores : matrice [commune*nMois + mois].
    assert len(v["scores"]) == 2 * 3
    assert list(v["scores"]) == [10, 20, 94, 0, 5, 0]
    # hasClay : 1 par commune (01001 argileuse, 01002 hors couverture).
    assert list(v["hasClay"]) == [1, 0]
    # round-trip des coordonnées (positions = lng,lat aplatis). Partie 2 (carré B, sommets 10-14)
    # commence au float d'index 20 ; son 1ᵉʳ sommet est (2,2).
    assert v["positions"][0] == 0.0 and v["positions"][1] == 0.0
    assert v["positions"][20] == 2.0 and v["positions"][21] == 2.0
    # header sérialisable + bbox cohérent.
    json.dumps(header)
    assert header["bbox"] == [0.0, 0.0, 3.0, 3.0]
    assert header["elevation"]["maxScore"] == 94


def test_assemble_empty() -> None:
    blob, header = assemble([], ["202401"], max_score=100, gamma=0.8, seuils=[25, 35, 47, 61])
    assert header["nCommunes"] == 0
    assert header["nParts"] == 0
    assert list(_views(blob, header)["partStarts"]) == [0]  # fermeture seule
    assert list(_views(blob, header)["hasClay"]) == []
