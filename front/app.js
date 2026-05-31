// Solveille — la boussole RGA (MapLibre + PMTiles). Choroplèthe de pression IP-RGA par mois,
// pilotée par un curseur de date ; fiche commune avec sparkline de pression.
"use strict";

// Protocole PMTiles
const protocol = new pmtiles.Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

const PMTILES_URL = "pmtiles://" + location.origin + "/tiles/communes.pmtiles";
const SRC_LAYER = "communes";

// Palette des 5 niveaux (code 1→5) ; 0/absent = gris (pas d'argile / hors couverture).
const GREY = "#e8e8e8";
const NIVEAU_COLORS = { 1: "#ffffb2", 2: "#fecc5c", 3: "#fd8d3c", 4: "#f03b20", 5: "#bd0026" };
const NIVEAU_LABELS = { 1: "Très faible", 2: "Faible", 3: "Modérée", 4: "Élevée", 5: "Très élevée" };

// Classes BRGM de l'IPS (niveau de nappe, 0 sec → 6 humide) — palette divergente brun↔teal.
const IPS_CLASS_LABELS = ["Très bas", "Bas", "Modérément bas", "Autour de la moyenne",
                          "Modérément haut", "Haut", "Très haut"];
const IPS_CLASS_BG = ["#8c510a", "#d8b365", "#f6e8c3", "#f5f5f5", "#c7eae5", "#5ab4ac", "#01665e"];
const IPS_CLASS_FG = ["#fff", "#1a1a2e", "#1a1a2e", "#1a1a2e", "#1a1a2e", "#1a1a2e", "#fff"];
// Part max de l'IPS dans T (= w_ips_max/(1+w_ips_max), w_ips_max=0.5) → normalise confiance_t.
const IPS_CONF_MAX = 1 / 3;
function ipsClasseInfo(c) {
  return { label: IPS_CLASS_LABELS[c] || "—", bg: IPS_CLASS_BG[c] || GREY, fg: IPS_CLASS_FG[c] || "#1a1a2e" };
}
// Couleur de la pastille H (calibration sécheresse) : neutre quand bas, chaude quand élevé.
function hChip(p) {
  if (p >= 0.6) return { bg: "#d7301f", fg: "#fff" };
  if (p >= 0.35) return { bg: "#fc8d59", fg: "#3a1500" };
  if (p >= 0.15) return { bg: "#fdcc8a", fg: "#3a1500" };
  return { bg: "#ece7e3", fg: "#5a544e" };
}
const FR_MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin",
                   "juil.", "août", "sept.", "oct.", "nov.", "déc."];

// Expression de couleur MapLibre pour l'attribut de niveau d'un mois (clé "n_AAAAMM").
function colorExpr(attrKey) {
  return [
    "match", ["coalesce", ["get", attrKey], 0],
    1, NIVEAU_COLORS[1], 2, NIVEAU_COLORS[2], 3, NIVEAU_COLORS[3],
    4, NIVEAU_COLORS[4], 5, NIVEAU_COLORS[5],
    GREY,
  ];
}

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
        attribution: "© OpenStreetMap, © CARTO — RGA: Géorisques/BRGM · SWI: Météo-France · Nappes: Hub'eau/ADES-BRGM · DVF: DGFiP/Etalab · IGN · Insee · SDES",
      },
      communes: { type: "vector", url: PMTILES_URL },
    },
    layers: [
      { id: "bg", type: "background", paint: { "background-color": "#f2f3f5" } },
      { id: "carto", type: "raster", source: "carto", paint: { "raster-opacity": 0.55 } },
      {
        id: "communes-fill", type: "fill", source: "communes", "source-layer": SRC_LAYER,
        paint: { "fill-color": GREY, "fill-opacity": 0.78 },
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

// --- État temporel (curseur de date) ---
let MONTHS = []; // [{key:'202512', iso:'2025-12', y, m}]
let idx = 0; // index du mois actif
let openInsee = null; // commune dont la fiche est ouverte
let lastSerie = null; // série de la commune ouverte (cache pour le sparkline)

const monthLabelOf = (mo) => FR_MONTHS[mo.m - 1] + " " + mo.y;

function monthsBetween(minIso, maxIso) {
  const [y0, m0] = minIso.slice(0, 7).split("-").map(Number);
  const [y1, m1] = maxIso.slice(0, 7).split("-").map(Number);
  const out = [];
  let y = y0, m = m0;
  while (y < y1 || (y === y1 && m <= m1)) {
    const mm = String(m).padStart(2, "0");
    out.push({ key: `${y}${mm}`, iso: `${y}-${mm}`, y, m });
    if (++m > 12) { m = 1; y++; }
  }
  return out;
}

const slider = document.getElementById("month");
const monthLabel = document.getElementById("monthLabel");
const legendMonth = document.getElementById("legendMonth");

function applyMonth(i, { refreshPanel = false } = {}) {
  if (!MONTHS.length) return;
  idx = Math.max(0, Math.min(MONTHS.length - 1, i));
  const mo = MONTHS[idx];
  if (map.getLayer("communes-fill")) {
    map.setPaintProperty("communes-fill", "fill-color", colorExpr("n_" + mo.key));
  }
  const lbl = monthLabelOf(mo);
  monthLabel.textContent = lbl;
  legendMonth.textContent = lbl;
  slider.value = String(idx);
  if (refreshPanel && openInsee) renderFiche();
}

slider.addEventListener("input", () => applyMonth(+slider.value)); // carte en direct (léger)
slider.addEventListener("change", () => applyMonth(+slider.value, { refreshPanel: true }));
document.getElementById("prevM").onclick = () => applyMonth(idx - 1, { refreshPanel: true });
document.getElementById("nextM").onclick = () => applyMonth(idx + 1, { refreshPanel: true });

async function initTime() {
  try {
    const meta = await (await fetch("/meta")).json();
    const md = meta.mois_disponibles;
    if (md && md.min && md.max) MONTHS = monthsBetween(md.min, md.max);
  } catch (_) { /* /meta indisponible : curseur masqué */ }
  if (!MONTHS.length) { document.querySelector(".timebar").style.display = "none"; return; }
  slider.min = "0";
  slider.max = String(MONTHS.length - 1);
  applyMonth(MONTHS.length - 1); // défaut : dernier mois
}
initTime();

// --- Helpers d'affichage ---
function niveauInfo(code) {
  if (!code) return { label: "Pas d'argile", color: GREY };
  return { label: NIVEAU_LABELS[code] || "—", color: NIVEAU_COLORS[code] || GREY };
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
  if (opts.id) e.id = opts.id;
  if (opts.text != null) e.textContent = opts.text;
  return e;
}
function kv(label, value) {
  const wrap = elt("div", { class: "kv" });
  wrap.append(elt("span", { class: "k", text: label }), elt("span", { class: "v", text: value }));
  return wrap;
}

// Sparkline SVG (score 0-100 dans le temps), point actif mis en évidence. Valeurs numériques
// uniquement → construction par DOM namespacé (pas d'innerHTML).
const SVGNS = "http://www.w3.org/2000/svg";
function sparkline(serie, activeIso) {
  const wrap = elt("div", { class: "spark" });
  const pts = serie.filter((p) => p.ip_rga_score != null);
  if (pts.length < 2) return wrap;
  const W = 300, H = 46, pad = 3;
  const svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "none");
  const x = (i) => pad + (i * (W - 2 * pad)) / (serie.length - 1);
  const y = (s) => H - pad - (s / 100) * (H - 2 * pad);
  const line = document.createElementNS(SVGNS, "polyline");
  line.setAttribute("fill", "none");
  line.setAttribute("stroke", "#bd0026");
  line.setAttribute("stroke-width", "1.5");
  line.setAttribute("points", serie.map((p, i) => `${x(i)},${y(p.ip_rga_score || 0)}`).join(" "));
  svg.appendChild(line);
  const ai = serie.findIndex((p) => p.date_mois.slice(0, 7) === activeIso);
  if (ai >= 0) {
    const dot = document.createElementNS(SVGNS, "circle");
    dot.setAttribute("cx", String(x(ai)));
    dot.setAttribute("cy", String(y(serie[ai].ip_rga_score || 0)));
    dot.setAttribute("r", "3");
    dot.setAttribute("fill", "#1a1a2e");
    svg.appendChild(dot);
  }
  wrap.appendChild(svg);
  const axis = elt("div", { class: "axis" });
  axis.append(
    elt("span", { text: serie[0].date_mois.slice(0, 7) }),
    elt("span", { text: "score de pression (0–100)" }),
    elt("span", { text: serie[serie.length - 1].date_mois.slice(0, 7) }),
  );
  wrap.appendChild(axis);
  return wrap;
}

const panel = document.getElementById("panel");

function openPanel(props) {
  openInsee = props.insee;
  lastSerie = null;
  panel.replaceChildren();

  const close = elt("button", { class: "close", text: "×" });
  close.setAttribute("aria-label", "Fermer");
  close.onclick = () => { panel.classList.remove("open"); openInsee = null; };
  panel.append(close);
  panel.append(elt("h2", { text: props.nom || props.insee }));
  panel.append(elt("div", {
    class: "dept",
    text: "Commune " + props.insee + (props.code_dept ? " · dépt " + props.code_dept : ""),
  }));
  if (truthy(props.basculement_2026)) {
    const b = elt("span", { class: "badge bascule", text: "Reclassée 2026" });
    panel.append(b);
  }
  panel.append(elt("div", { class: "note", id: "fiche", text: "Chargement de la fiche…" }));
  panel.classList.add("open");
  renderFiche();
}

async function renderFiche() {
  if (!openInsee || !panel.classList.contains("open")) return;
  const mo = MONTHS[idx];
  const slot = document.getElementById("fiche");
  if (!slot) return;
  try {
    const r = await fetch("/communes/" + encodeURIComponent(openInsee) + (mo ? "?mois=" + mo.iso : ""));
    if (!r.ok) throw new Error(String(r.status));
    const f = await r.json();
    const body = elt("div");

    // Pastille de pression du mois sélectionné
    const ni = niveauInfo(f.ip_rga_niveau_code);
    const head = elt("div");
    const pill = elt("span", { class: "e-pill", text: "Pression " + (f.ip_rga_niveau || ni.label) });
    pill.style.background = ni.color;
    if (ni.color === "#ffffb2" || ni.color === GREY) pill.style.color = "#1a1a2e";
    head.append(pill);
    if (mo) head.append(elt("span", { class: "dept", text: "  " + monthLabelOf(mo) }));
    body.append(head);

    // Sparkline (série en cache)
    if (!lastSerie) {
      try {
        lastSerie = (await (await fetch("/communes/" + encodeURIComponent(openInsee) + "/serie")).json()).serie;
      } catch (_) { lastSerie = []; }
    }
    if (lastSerie && lastSerie.length > 1) body.append(sparkline(lastSerie, mo ? mo.iso : ""));

    body.append(
      kv("Score de pression", f.ip_rga_score == null ? "—" : f.ip_rga_score + " / 100"),
      kv("Sécheresse du moment (T)", f.T == null ? "—" : Math.round(f.T * 100) + " %"),
      kv("Exposition argile (E)", f.E == null ? "—" : f.E.toFixed(2)),
      kv("Surface en aléa moyen+fort", pct(f.part_alea_moyen_fort)),
      kv("Maisons exposées (est.)", num(f.n_maisons_exposees)),
      kv("Valeur de bâti exposé", euros(f.valeur_bati_exposee_eur)),
      kv("Prix médian maison", f.prix_median_maison_eur_m2 ? Math.round(f.prix_median_maison_eur_m2) + " €/m²" : "—"),
      kv("Reclassement 2026", f.basculement_2026
        ? `${f.rga_classe_2020}→${f.rga_classe_2026} (${(f.bascule_type || "").replace(/_/g, " ")})`
        : "non"),
    );
    // Corroboration nappe (IPS) — affichée là où une station piézométrique est représentative.
    if (f.confiance_t > 0 && f.ips_classe != null) {
      const ic = ipsClasseInfo(f.ips_classe);
      const ratio = Math.min(1, f.confiance_t / IPS_CONF_MAX);
      const confLbl = ratio >= 0.66 ? "forte" : ratio >= 0.33 ? "modérée" : "faible";
      const renforce = f.dry_ips != null && f.dry_swi != null && f.dry_ips > f.dry_swi;
      const blk = elt("div", { class: "ips-block" });
      const headIps = elt("div", { class: "ips-head" });
      headIps.append(elt("span", { class: "k", text: "Nappe (IPS local)" }));
      const chip = elt("span", { class: "ips-chip", text: ic.label });
      chip.style.background = ic.bg;
      chip.style.color = ic.fg;
      headIps.append(chip);
      blk.append(headIps);
      blk.append(elt("div", {
        class: "ips-note",
        text: "Corroboration piézométrique " + confLbl + " — le niveau des nappes "
          + (renforce ? "renforce" : "tempère") + " la tension du sol. "
          + "IPS standardisé recalculé (Hub'eau / ADES-BRGM), mis à jour quotidiennement.",
      }));
      body.append(blk);
    }
    // Calibration historique (H — Cat-Nat sécheresse). h_proba est gaté E>0 au mart.
    if (f.h_proba != null) {
      const pctH = Math.round(f.h_proba * 100);
      const ch = hChip(f.h_proba);
      const blk = elt("div", { class: "h-block" });
      const headH = elt("div", { class: "h-head" });
      headH.append(elt("span", { class: "k", text: "Calibration historique (sécheresse)" }));
      const chip = elt("span", { class: "h-chip", text: pctH + " %" });
      chip.style.background = ch.bg;
      chip.style.color = ch.fg;
      headH.append(chip);
      blk.append(headH);
      const ref = f.h_pool_level === "national" ? "(référence nationale)" : "dans le département";
      blk.append(elt("div", {
        class: "h-note",
        text: "La sécheresse de ce mois correspond à " + pctH + " % des situations passées ayant "
          + "conduit à une reconnaissance Cat-Nat sécheresse " + ref + ". Lecture indicative — "
          + "pas une probabilité de reconnaissance (critères aussi administratifs).",
      }));
      const freq = f.catnat_freq || 0;
      let hist;
      if (freq > 0) {
        const an = f.annees_reco || [];
        const span = an.length ? " (" + an[0] + "→" + an[an.length - 1] + ")" : "";
        hist = freq + " arrêté" + (freq > 1 ? "s" : "") + " sécheresse ici" + span
          + (f.dernier_arrete ? ", dernier le " + f.dernier_arrete : "") + ".";
      } else {
        hist = "Aucun arrêté sécheresse recensé pour cette commune (GASPAR).";
      }
      blk.append(elt("div", { class: "h-hist", text: hist }));
      body.append(blk);
    }
    if (f.has_rga_coverage === false) {
      body.append(elt("div", {
        class: "note",
        text: "⚠️ Hors couverture du zonage RGA (ex. Paris) : pression non mesurée, pas un « 0 » avéré.",
      }));
    }
    body.append(elt("div", {
      class: "note",
      text: "Pression = exposition argile × sécheresse du moment (SWI sol, nowcast lissé 3 mois ; "
        + "+ IPS nappes là où une station est représentative). Calibration historique vs arrêtés "
        + "Cat-Nat sécheresse (GASPAR/DGPR). Indice indicatif. Sources : "
        + "Géorisques/BRGM, GASPAR/DGPR, Météo-France (SWI), Hub'eau/ADES-BRGM (nappes), IGN, "
        + "Insee, SDES/Fidéli, DGFiP/Etalab. Agrégats communaux (DVF).",
    }));
    slot.replaceWith(body);
    body.id = "fiche";
  } catch (err) {
    slot.textContent = "Fiche indisponible (" + err.message + ").";
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
