// Solveille — carte de l'enjeu RGA (MapLibre + PMTiles). Choroplèthe E + reclassées 2026.
"use strict";

// Protocole PMTiles
const protocol = new pmtiles.Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

const PMTILES_URL = "pmtiles://" + location.origin + "/tiles/communes.pmtiles";
const SRC_LAYER = "communes";

// Couleur de E : 0 / inconnu = gris ; sinon paliers séquentiels.
const E_COLOR = [
  "case",
  ["any", ["!", ["has", "E"]], ["==", ["get", "E"], 0]], "#e8e8e8",
  ["step", ["get", "E"],
    "#ffffb2", 0.2, "#fecc5c", 0.4, "#fd8d3c", 0.6, "#f03b20", 0.8, "#bd0026"],
];

const map = new maplibregl.Map({
  container: "map",
  style: {
    version: 8,
    glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
    sources: {
      carto: {
        type: "raster",
        tiles: ["https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png"],
        tileSize: 256,
        attribution: "© OpenStreetMap, © CARTO — RGA: Géorisques/BRGM · DVF: DGFiP/Etalab · IGN · Insee · SDES",
      },
      communes: { type: "vector", url: PMTILES_URL },
    },
    layers: [
      { id: "bg", type: "background", paint: { "background-color": "#f2f3f5" } },
      { id: "carto", type: "raster", source: "carto", paint: { "raster-opacity": 0.55 } },
      {
        id: "communes-fill", type: "fill", source: "communes", "source-layer": SRC_LAYER,
        paint: { "fill-color": E_COLOR, "fill-opacity": 0.72 },
      },
      {
        id: "communes-line", type: "line", source: "communes", "source-layer": SRC_LAYER,
        paint: { "line-color": "#ffffff", "line-width": 0.3, "line-opacity": 0.5 },
      },
      {
        id: "communes-bascule", type: "line", source: "communes", "source-layer": SRC_LAYER,
        filter: ["==", ["get", "basculement_2026"], true],
        paint: { "line-color": "#6d28d9", "line-width": 2 },
      },
    ],
  },
  center: [2.4, 46.6],
  zoom: 4.6,
  maxZoom: 12,
});
map.addControl(new maplibregl.NavigationControl(), "bottom-right");

// --- Helpers d'affichage ---
function eInfo(E) {
  if (E == null) return { label: "—", color: "#999" };
  if (E === 0) return { label: "Pas d'argile", color: "#888" };
  if (E < 0.2) return { label: "Très faible", color: "#e0c200" };
  if (E < 0.4) return { label: "Faible", color: "#fecc5c" };
  if (E < 0.6) return { label: "Modérée", color: "#fd8d3c" };
  if (E < 0.8) return { label: "Élevée", color: "#f03b20" };
  return { label: "Très élevée", color: "#bd0026" };
}
function euros(v) {
  if (v == null) return "—";
  if (v >= 1e9) return (v / 1e9).toFixed(1).replace(".", ",") + " Md€";
  if (v >= 1e6) return (v / 1e6).toFixed(0) + " M€";
  return Math.round(v).toLocaleString("fr-FR") + " €";
}
const pct = (v) => (v == null ? "—" : Math.round(v * 100) + " %");
const num = (v) => (v == null ? "—" : Math.round(v).toLocaleString("fr-FR"));
const truthy = (v) => v === true || v === "true";

// DOM sûr : aucune interpolation HTML, tout le texte via textContent.
function elt(tag, opts = {}) {
  const e = document.createElement(tag);
  if (opts.class) e.className = opts.class;
  if (opts.text != null) e.textContent = opts.text;
  return e;
}
function kv(label, value) {
  const wrap = elt("div", { class: "kv" });
  wrap.append(elt("span", { class: "k", text: label }), elt("span", { class: "v", text: value }));
  return wrap;
}

const panel = document.getElementById("panel");

async function openPanel(props) {
  const insee = props.insee;
  const e = eInfo(props.E == null ? null : Number(props.E));
  panel.replaceChildren();

  const close = elt("button", { class: "close", text: "×" });
  close.setAttribute("aria-label", "Fermer");
  close.onclick = () => panel.classList.remove("open");
  panel.append(close);
  panel.append(elt("h2", { text: props.nom || insee }));
  panel.append(elt("div", {
    class: "dept",
    text: "Commune " + insee + (props.code_dept ? " · dépt " + props.code_dept : ""),
  }));

  const head = elt("div");
  const pill = elt("span", { class: "e-pill", text: "Exposition " + e.label });
  pill.style.background = e.color;
  head.append(pill);
  if (truthy(props.basculement_2026)) {
    const b = elt("span", { class: "badge bascule", text: "Reclassée 2026" });
    b.style.marginLeft = "8px";
    head.append(b);
  }
  panel.append(head);

  const fiche = elt("div", { class: "note", text: "Chargement de la fiche…" });
  panel.append(fiche);
  panel.classList.add("open");

  try {
    const r = await fetch("/communes/" + encodeURIComponent(insee));
    if (!r.ok) throw new Error(String(r.status));
    const f = await r.json();
    const body = elt("div");
    body.append(
      kv("Indice d'exposition E", f.E == null ? "—" : f.E.toFixed(2)),
      kv("Surface en aléa moyen+fort", pct(f.part_alea_moyen_fort)),
      kv("Classe dominante", f.classe_dominante || "—"),
      kv("Maisons exposées (est.)", num(f.n_maisons_exposees)),
      kv("Valeur de bâti exposé", euros(f.valeur_bati_exposee_eur)),
      kv("Prix médian maison", f.prix_median_maison_eur_m2 ? Math.round(f.prix_median_maison_eur_m2) + " €/m²" : "—"),
      kv("Reclassement 2026", f.basculement_2026
        ? `${f.rga_classe_2020}→${f.rga_classe_2026} (${(f.bascule_type || "").replace(/_/g, " ")})`
        : "non"),
    );
    if (f.has_rga_coverage === false) {
      body.append(elt("div", {
        class: "note",
        text: "⚠️ Hors couverture du zonage RGA (ex. Paris) : exposition non mesurée, pas un « 0 » avéré.",
      }));
    }
    body.append(elt("div", {
      class: "note",
      text: "Indice indicatif (exposition × enjeu), sans la composante dynamique (v1). "
        + "Sources : Géorisques/BRGM, IGN, Insee, SDES/Fidéli, DGFiP/Etalab. Agrégats communaux (DVF).",
    }));
    fiche.replaceWith(body);
  } catch (err) {
    fiche.textContent = "Fiche indisponible (" + err.message + ").";
  }
}

// --- Interactions carte ---
map.on("click", "communes-fill", (e) => { if (e.features[0]) openPanel(e.features[0].properties); });
map.on("mouseenter", "communes-fill", () => { map.getCanvas().style.cursor = "pointer"; });
map.on("mouseleave", "communes-fill", () => { map.getCanvas().style.cursor = ""; });

// --- Recherche (code INSEE ou nom) ---
function bboxOf(geom) {
  let xmin = 180, ymin = 90, xmax = -180, ymax = -90;
  const walk = (c) => {
    if (typeof c[0] === "number") {
      xmin = Math.min(xmin, c[0]); xmax = Math.max(xmax, c[0]);
      ymin = Math.min(ymin, c[1]); ymax = Math.max(ymax, c[1]);
    } else c.forEach(walk);
  };
  walk(geom.coordinates);
  return [[xmin, ymin], [xmax, ymax]];
}

document.getElementById("search").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const q = document.getElementById("q").value.trim();
  if (!q) return;
  const isInsee = /^\d[\dAB]\d{3}$/i.test(q);
  const feats = map.querySourceFeatures("communes", { sourceLayer: SRC_LAYER }).filter((f) => {
    const p = f.properties;
    return isInsee ? p.insee === q.toUpperCase() : (p.nom || "").toLowerCase() === q.toLowerCase();
  });
  if (feats.length) {
    map.fitBounds(bboxOf(feats[0].geometry), { padding: 80, maxZoom: 11 });
    openPanel(feats[0].properties);
  } else if (isInsee) {
    openPanel({ insee: q.toUpperCase() });
  } else {
    alert("Commune non trouvée à ce zoom — dézoome sur la métropole et réessaie, ou saisis le code INSEE.");
  }
});
