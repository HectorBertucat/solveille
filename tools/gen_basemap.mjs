// Générateur ONE-SHOT du fond de carte VECTORIEL self-hosté (B-vec).
//
// Produit `front/basemap-layers.js`, qui pose un global `window.PM_BASEMAP = {light:[…], dark:[…]}`
// = les tableaux de couches MapLibre du basemap Protomaps (flavors clair/sombre, libellés `fr`).
// `front/app.js` insère le choroplèthe SOUS la 1ʳᵉ couche `symbol` de ces tableaux → les noms de
// lieux passent AU-DESSUS du fill (lecture pro, type Datawrapper/FT).
//
// On RETIRE toute référence d'icône (sprite) : on supprime les couches purement décoratives à
// icône (sens unique, écussons de route, POIs) et on dé-iconise les autres (ex. `places_locality`,
// dont on garde le LABEL). Résultat : AUCUN sprite à self-host — seuls les glyphs le sont
// (`make glyphs`). Le style ne dépend ainsi que de `/tiles/france.pmtiles` + `/glyphs/…` (0 CDN).
//
// Régénérer (rare — seulement si on bump @protomaps/basemaps) :
//   cd tools && npm install && node gen_basemap.mjs
// La version du paquet est épinglée dans tools/package.json. Sortie commitée (statique, ~65 Ko/thème).

import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { layers, LIGHT, DARK } from "@protomaps/basemaps";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = join(HERE, "..", "front", "basemap-layers.js");

// Couches purement décoratives nécessitant un sprite → retirées (inutiles sur un choroplèthe).
const DROP = new Set(["roads_oneway", "roads_shields", "pois"]);

// Retire toute clé d'icône d'une couche symbol (on conserve le texte) → plus aucun sprite requis.
function deIconize(layer) {
  if (layer.layout) {
    for (const k of Object.keys(layer.layout)) if (k.startsWith("icon-")) delete layer.layout[k];
  }
  if (layer.paint) {
    for (const k of Object.keys(layer.paint)) if (k.startsWith("icon-")) delete layer.paint[k];
  }
  return layer;
}

function build(flavor) {
  return layers("protomaps", flavor, { lang: "fr" })
    .filter((l) => !DROP.has(l.id))
    .map(deIconize);
}

const out = { light: build(LIGHT), dark: build(DARK) };

const header =
  "// GÉNÉRÉ par tools/gen_basemap.mjs depuis @protomaps/basemaps@5.7.2 (flavors light/dark, lang fr).\n" +
  "// Icônes/sprite retirés ; libellés conservés. NE PAS éditer à la main — voir tools/gen_basemap.mjs.\n";
writeFileSync(OUT, header + "window.PM_BASEMAP = " + JSON.stringify(out) + ";\n");

const sym = (a) => a.filter((l) => l.type === "symbol").length;
console.log(
  `Écrit ${OUT}\n  light: ${out.light.length} couches (${sym(out.light)} labels)` +
    `\n  dark : ${out.dark.length} couches (${sym(out.dark)} labels)`,
);
