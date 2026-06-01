"""Contrat du manifest d'assets (B-perf) — fonctions pures + build sur fichiers temporaires.

Vérifie le hash de contenu (déterministe, 8 hex), le rendu `window.SOLVEILLE_ASSETS = {…};`
(JSON parsable) et l'omission des assets absents.
"""

from __future__ import annotations

import io
import json
import re

from solveille.transform.build_assets import build_assets, hash_stream, render_manifest


def test_hash_stream_deterministic_and_short() -> None:
    h1 = hash_stream(io.BytesIO(b"hello world"))
    h2 = hash_stream(io.BytesIO(b"hello world"))
    h3 = hash_stream(io.BytesIO(b"hello WORLD"))
    assert h1 == h2 and h1 != h3
    assert len(h1) == 8 and re.fullmatch(r"[0-9a-f]{8}", h1)


def test_hash_stream_chunk_boundary() -> None:
    # Plus gros qu'un chunk de 1 Mo : le streaming doit donner le même hash qu'en un bloc.
    data = b"x" * (3 * (1 << 20) + 123)
    assert hash_stream(io.BytesIO(data)) == hash_stream(io.BytesIO(data))


def test_render_manifest_is_parseable_global() -> None:
    js = render_manifest({"communes.pmtiles": "abc12345", "france.pmtiles": "def67890"})
    assert js.startswith("window.SOLVEILLE_ASSETS = ")
    m = re.search(r"window\.SOLVEILLE_ASSETS = (\{.*\});", js)
    assert m, "format inattendu"
    obj = json.loads(m.group(1))
    assert obj["communes.pmtiles"] == "abc12345"
    assert obj["france.pmtiles"] == "def67890"


def test_build_assets_omits_absent(tmp_path) -> None:
    tiles = tmp_path / "tiles"
    front = tmp_path / "front"
    tiles.mkdir()
    front.mkdir()
    # Seuls communes.pmtiles + communes-index.json présents → les autres sont omis.
    (tiles / "communes.pmtiles").write_bytes(b"PMTILES-CONTENT")
    (front / "communes-index.json").write_bytes(b'{"x":1}')
    out = build_assets(tiles_dir=tiles, front_dir=front)
    assert out == front / "assets.js"
    obj = json.loads(re.search(r"= (\{.*\});", out.read_text()).group(1))
    assert set(obj) == {"communes.pmtiles", "communes-index.json"}  # france/3d absents → omis
    assert all(re.fullmatch(r"[0-9a-f]{8}", v) for v in obj.values())
