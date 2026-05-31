"""Contrat du fond de carte vectoriel (B-vec), vérifié SANS réseau.

Garde-fou #1 : **un seul glyph 404 fait écran noir sur TOUTE la carte**. On vérifie donc que chaque
`text-font` référencé par le style généré (`front/basemap-layers.js`) correspond à un fontstack que
`build_glyphs.py` télécharge — sinon MapLibre demanderait des glyphs absents. On vérifie aussi
qu'AUCUNE couche ne référence d'icône (`icon-image`), car on ne self-host pas de sprite (icônes
retirées par `tools/gen_basemap.mjs`)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from solveille.transform.build_glyphs import _FONTSTACKS

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BASEMAP_JS = _REPO_ROOT / "front" / "basemap-layers.js"

# Nom de fontstack Noto Sans dans un `text-font` (chaîne littérale ou imbriqué dans une expression).
_FONT_NAME_RE = re.compile(r'"(Noto Sans [^"]+)"')


def _load_basemap() -> dict[str, list[dict]]:
    """Parse `window.PM_BASEMAP = {…};` → l'objet {light:[…], dark:[…]}."""
    text = _BASEMAP_JS.read_text(encoding="utf-8")
    m = re.search(r"window\.PM_BASEMAP\s*=\s*(\{.*\});\s*$", text, re.DOTALL)
    assert m, "front/basemap-layers.js : format inattendu (régénérer via tools/gen_basemap.mjs)"
    return json.loads(m.group(1))


def _iter_text_fonts(layers: list[dict]) -> set[str]:
    """Tous les noms de fonte référencés par les `text-font` (y compris dans des expressions)."""
    fonts: set[str] = set()
    for layer in layers:
        tf = (layer.get("layout") or {}).get("text-font")
        if tf is None:
            continue
        # `text-font` peut être une liste de chaînes OU une expression MapLibre imbriquée → on
        # ratisse toutes les chaînes "Noto Sans …" du JSON sérialisé de la valeur.
        fonts.update(_FONT_NAME_RE.findall(json.dumps(tf, ensure_ascii=False)))
    return fonts


def test_basemap_js_present_and_parses() -> None:
    pm = _load_basemap()
    assert set(pm) == {"light", "dark"}
    assert pm["light"] and pm["dark"], "tableaux de couches vides"


def test_every_referenced_fontstack_is_downloaded() -> None:
    pm = _load_basemap()
    referenced = _iter_text_fonts(pm["light"]) | _iter_text_fonts(pm["dark"])
    assert referenced, "aucun text-font trouvé (style cassé ?)"
    missing = referenced - set(_FONTSTACKS)
    assert not missing, (
        f"text-font non téléchargés (_FONTSTACKS) : {sorted(missing)} → glyph 404 = carte noire. "
        "Ajouter le fontstack à _FONTSTACKS (et vérifier qu'il existe dans basemaps-assets)."
    )


def test_no_sprite_icon_reference() -> None:
    pm = _load_basemap()
    for theme, layers in pm.items():
        for layer in layers:
            layout = layer.get("layout") or {}
            assert "icon-image" not in layout, (
                f"{theme}/{layer.get('id')} a une icône (icon-image) or on ne self-host pas de "
                "sprite → dé-iconiser dans tools/gen_basemap.mjs."
            )
