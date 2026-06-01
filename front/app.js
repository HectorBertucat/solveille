// Solveille — la boussole RGA (MapLibre + PMTiles). Choroplèthe de pression IP-RGA par mois,
// pilotée par un curseur de date ; fiche commune avec sparkline de pression.
"use strict";

// Protocole PMTiles
const protocol = new pmtiles.Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

const PMTILES_URL = "pmtiles://" + location.origin + "/tiles/communes.pmtiles";
const SRC_LAYER = "communes";

// Palette des 5 niveaux : **lue depuis les tokens CSS** (--risk-0..5) → source unique UI+carte (B3).
function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
let GREY = "#e7e3dc"; // = --risk-0 (neutre « papier ») ; rafraîchi par readPalette()
const NIVEAU_COLORS = { 1: "#fde8c4", 2: "#f9c178", 3: "#f08c3a", 4: "#d6562a", 5: "#9b2226" };
function readPalette() {
  GREY = cssVar("--risk-0") || GREY;
  for (let i = 1; i <= 5; i++) NIVEAU_COLORS[i] = cssVar("--risk-" + i) || NIVEAU_COLORS[i];
}
const NIVEAU_LABELS = { 1: "Très faible", 2: "Faible", 3: "Modérée", 4: "Élevée", 5: "Très élevée" };
// Texte clair sur fond foncé (niveaux 3-5), sinon encre (0-2).
function pillInk(code) { return code >= 3 ? "#fff" : "#241c14"; }
// Hauteurs 3D (m) par niveau : « montagnes de pression » (B1, vue fill-extrusion).
const NIVEAU_HEIGHT = { 1: 3000, 2: 9000, 3: 18000, 4: 28000, 5: 40000 };
function heightExpr(attrKey) {
  return [
    "match", ["get", attrKey],
    1, NIVEAU_HEIGHT[1], 2, NIVEAU_HEIGHT[2], 3, NIVEAU_HEIGHT[3],
    4, NIVEAU_HEIGHT[4], 5, NIVEAU_HEIGHT[5],
    0,
  ];
}

// Carte BIVARIÉE Exposition × Tension (B1) : matrice 3×3 (clé = (e_bin-1)*3 + t_bin, 1→9).
// Diverge : faible expo + sécheresse = neutralisé (teal) ; forte expo + sécheresse = danger (brique).
const BIV = {
  1: "#eae7e0", 2: "#cfdbd5", 3: "#a6c5bf", // e=1 (faible expo) : neutre → teal
  4: "#e2cf9c", 5: "#d49a5b", 6: "#c46a39", // e=2 (expo moyenne)
  7: "#cf9a2e", 8: "#bf5d28", 9: "#8c1c20", // e=3 (forte expo) : ocre → brique (danger actif)
};
function bivExpr(tKey) {
  const e = ["get", "e_bin"];
  const t = ["coalesce", ["get", tKey], 0];
  // clé 1-9 si e>0 et t>0, sinon 0 (neutre : pas d'argile ou pas de tension mesurée).
  const key = ["case", ["all", [">", e, 0], [">", t, 0]], ["+", ["*", ["-", e, 1], 3], t], 0];
  return [
    "match", key,
    1, BIV[1], 2, BIV[2], 3, BIV[3], 4, BIV[4], 5, BIV[5], 6, BIV[6], 7, BIV[7], 8, BIV[8], 9, BIV[9],
    GREY,
  ];
}

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
// Sans `coalesce` : le pivot écrit déjà 0 si NULL, et `match` retombe sur GREY pour 0/absent.
function colorExpr(attrKey) {
  return [
    "match", ["get", attrKey],
    1, NIVEAU_COLORS[1], 2, NIVEAU_COLORS[2], 3, NIVEAU_COLORS[3],
    4, NIVEAU_COLORS[4], 5, NIVEAU_COLORS[5],
    GREY,
  ];
}

// Thème (clair/sombre) appliqué AVANT la carte pour que readPalette() lise les bons tokens.
const savedTheme = (() => { try { return localStorage.getItem("solveille_theme"); } catch (_) { return null; } })();
if (savedTheme === "dark") document.documentElement.dataset.theme = "dark";
readPalette();
const themeNow = () => (document.documentElement.dataset.theme === "dark" ? "dark" : "light");
// --- Fond de carte VECTORIEL self-hosté (B-vec). Remplace le raster CARTO : les LIBELLÉS du
// basemap (villes, départements, eaux) passent AU-DESSUS du choroplèthe (insérés sous la 1ʳᵉ
// couche `symbol`) → lecture pro, type Datawrapper/FT. Tuiles Protomaps + glyphs Noto Sans, 100 %
// servis depuis notre origine (0 CDN runtime) : `/tiles/france.pmtiles` + `/glyphs/…`. ---
const BASEMAP_PMTILES = "pmtiles://" + location.origin + "/tiles/france.pmtiles";
const GLYPHS_URL = location.origin + "/glyphs/{fontstack}/{range}.pbf";
const BASEMAP_ATTRIB =
  "© OpenStreetMap (ODbL), Protomaps — RGA: Géorisques/BRGM · SWI: Météo-France · " +
  "Nappes: Hub'eau/ADES-BRGM · DVF: DGFiP/Etalab · IGN · Insee · SDES";

// Nos 4 couches de données, insérées sous les labels du basemap. `fill-opacity` 0.62 (vs 0.78
// avant) : laisse respirer roads/eaux/labels du fond vectoriel sous le tint de pression.
function communesLayers() {
  return [
    {
      id: "communes-fill", type: "fill", source: "communes", "source-layer": SRC_LAYER,
      // Transition courte : lisse le saut de couleur d'un mois à l'autre (prev/next, play).
      paint: { "fill-color": GREY, "fill-opacity": 0.62, "fill-color-transition": { duration: 120, delay: 0 } },
    },
    {
      id: "communes-line", type: "line", source: "communes", "source-layer": SRC_LAYER,
      paint: { "line-color": "rgba(80,60,40,0.20)", "line-width": 0.3 },
    },
    {
      id: "communes-bascule", type: "line", source: "communes", "source-layer": SRC_LAYER,
      filter: ["==", ["get", "basculement_2026"], true],
      paint: { "line-color": "#6d28d9", "line-width": 2 },
    },
    {
      // 3D « montagnes de pression » (B1) : masquée par défaut, activée par le toggle 3D.
      id: "communes-3d", type: "fill-extrusion", source: "communes", "source-layer": SRC_LAYER,
      layout: { visibility: "none" },
      paint: {
        "fill-extrusion-color": GREY, "fill-extrusion-opacity": 0.85,
        "fill-extrusion-height": 0,
        "fill-extrusion-height-transition": { duration: 200, delay: 0 },
      },
    },
  ];
}

// Style complet pour un thème : couches Protomaps (flavor clair/sombre, posées par
// front/basemap-layers.js) + nos couches insérées juste AVANT la 1ʳᵉ couche `symbol` → les noms
// de lieux restent au-dessus du fill. Repli gracieux (juste fond + choroplèthe) si le JS du
// basemap n'a pas chargé.
function buildStyle(theme) {
  const pm = window.PM_BASEMAP; // { light:[…], dark:[…] }
  const base = (pm ? (theme === "dark" ? pm.dark : pm.light) : []).map((l) => ({ ...l }));
  const firstSym = base.findIndex((l) => l.type === "symbol");
  const cut = firstSym < 0 ? base.length : firstSym;
  const ours = communesLayers();
  const layers = pm
    ? [...base.slice(0, cut), ...ours, ...base.slice(cut)]
    : [{ id: "bg", type: "background", paint: { "background-color": cssVar("--bg") } }, ...ours];
  return {
    version: 8,
    glyphs: GLYPHS_URL,
    sources: {
      protomaps: { type: "vector", url: BASEMAP_PMTILES, attribution: BASEMAP_ATTRIB },
      communes: { type: "vector", url: PMTILES_URL },
    },
    layers,
  };
}

const map = new maplibregl.Map({
  container: "map",
  style: buildStyle(themeNow()),
  // CJK rendu via la fonte système → pas de requête (ni 404) sur les plages d'idéogrammes.
  localIdeographFontFamily: "sans-serif",
  center: [2.4, 46.6],
  zoom: 4.6,
  minZoom: 4, // communes z4→9 (overzoom au-delà) : sous z4 pas de tuiles communes
  maxZoom: 12,
});
map.addControl(new maplibregl.NavigationControl(), "bottom-right");

// --- État temporel (curseur de date) ---
let MONTHS = []; // [{key:'202512', iso:'2025-12', y, m}]
let EXPRS = []; // expressions de couleur MapLibre pré-compilées (1 par mois) — voir A3
let BIV_EXPRS = []; // idem pour le mode bivarié Exposition × Tension (B1)
let mapMode = "pression"; // "pression" | "biv"
let META = null; // /meta (last_updated_* pour l'overlay « À propos »)
let CP_DATE = null; // last_updated_cp (depuis communes-index.json)
let HIST = {}; // histogramme mensuel des niveaux {AAAAMM:[n1..n5]} → compteur de légende
let is3D = false; // vue 3D (fill-extrusion) active ?
let idx = 0; // index du mois actif
let openInsee = null; // commune dont la fiche est ouverte
let openNom = null; // nom de la commune ouverte (libellé du comparateur)
let lastSerie = null; // série de la commune ouverte (cache pour le sparkline)
let compareSet = []; // [{insee, nom, serie}] communes épinglées (comparateur B-plot) ; persiste

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
const legendCount = document.getElementById("legendCount");

// Compteur de légende : « ≈ N communes en pression élevée+ ce mois » (histogramme mensuel).
function updateLegendCount() {
  if (!legendCount) return;
  const mo = MONTHS[idx];
  const h = mo && HIST[mo.key];
  legendCount.replaceChildren();
  if (!h) return;
  const eleve = (h[3] || 0) + (h[4] || 0);
  legendCount.append(
    document.createTextNode("≈ "),
    elt("b", { text: eleve.toLocaleString("fr-FR") }),
    document.createTextNode(" communes en pression élevée+ ce mois"),
  );
}

// Recolore la carte + met à jour les libellés pour le mois `i` (expression pré-compilée).
function paintMonth(i) {
  idx = Math.max(0, Math.min(MONTHS.length - 1, i));
  const mo = MONTHS[idx];
  const expr = mapMode === "biv"
    ? (BIV_EXPRS[idx] || bivExpr("t_" + mo.key))
    : (EXPRS[idx] || colorExpr("n_" + mo.key));
  if (map.getLayer("communes-fill")) map.setPaintProperty("communes-fill", "fill-color", expr);
  if (is3D && map.getLayer("communes-3d")) {
    map.setPaintProperty("communes-3d", "fill-extrusion-color", expr); // couleur selon le mode
    map.setPaintProperty("communes-3d", "fill-extrusion-height", heightExpr("n_" + mo.key)); // relief = niveau
  }
  const lbl = monthLabelOf(mo);
  monthLabel.textContent = lbl;
  legendMonth.textContent = lbl;
  updateLegendCount();
  slider.value = String(idx);
}

// Throttle requestAnimationFrame : l'event `input` peut tirer plusieurs fois/frame, mais on ne
// recolore qu'UNE fois par frame (le vrai levier de fluidité — voir A3). `pendingIdx` mémorise
// la dernière valeur demandée pendant qu'une frame est en vol.
let rafPending = false;
let pendingIdx = null;
function scheduleApply(i) {
  pendingIdx = i;
  if (rafPending) return;
  rafPending = true;
  requestAnimationFrame(() => {
    rafPending = false;
    if (pendingIdx != null) { paintMonth(pendingIdx); pendingIdx = null; }
  });
}

function applyMonth(i, { refreshPanel = false } = {}) {
  if (!MONTHS.length) return;
  pendingIdx = null; // annule une recolorisation rAF en attente (on peint tout de suite)
  paintMonth(i);
  if (refreshPanel && openInsee) renderFiche();
}

slider.addEventListener("input", () => { stopPlay(); scheduleApply(+slider.value); }); // throttlé rAF
slider.addEventListener("change", () => applyMonth(+slider.value, { refreshPanel: true }));
document.getElementById("prevM").onclick = () => { stopPlay(); applyMonth(idx - 1, { refreshPanel: true }); };
document.getElementById("nextM").onclick = () => { stopPlay(); applyMonth(idx + 1, { refreshPanel: true }); };

// --- Animation « play » (B1) : boucle les mois (la transition fill-color lisse le saut). ---
let playTimer = null;
function stopPlay() {
  if (!playTimer) return;
  clearInterval(playTimer);
  playTimer = null;
  const b = document.getElementById("playM");
  if (b) b.textContent = "▶";
}
document.getElementById("playM").addEventListener("click", () => {
  if (playTimer) { stopPlay(); return; }
  if (!MONTHS.length) return;
  document.getElementById("playM").textContent = "⏸";
  if (idx >= MONTHS.length - 1) paintMonth(0); // repart du début si on est à la fin
  playTimer = setInterval(() => {
    if (idx >= MONTHS.length - 1) { stopPlay(); applyMonth(idx, { refreshPanel: true }); return; }
    paintMonth(idx + 1);
  }, 220);
});

async function initTime() {
  try {
    const meta = await (await fetch("/meta")).json();
    META = meta;
    fillIntroDates();
    const md = meta.mois_disponibles;
    if (md && md.min && md.max) MONTHS = monthsBetween(md.min, md.max);
  } catch (_) { /* /meta indisponible : curseur masqué */ }
  if (!MONTHS.length) { document.querySelector(".timebar").style.display = "none"; return; }
  EXPRS = MONTHS.map((m) => colorExpr("n_" + m.key)); // pré-compile une fois (pas par tick)
  BIV_EXPRS = MONTHS.map((m) => bivExpr("t_" + m.key)); // idem pour le mode bivarié
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
function sparkline(serie, activeIso, eventYears) {
  const wrap = elt("div", { class: "spark" });
  const pts = serie.filter((p) => p.ip_rga_score != null);
  if (pts.length < 2) return wrap;
  const W = 300, H = 46, pad = 3;
  const svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "none");
  const x = (i) => pad + (i * (W - 2 * pad)) / (serie.length - 1);
  const y = (s) => H - pad - (s / 100) * (H - 2 * pad);
  // Marqueurs d'arrêtés Cat-Nat sécheresse (années de reconnaissance dans la fenêtre affichée) :
  // tics violets verticaux → relie l'historique GASPAR au fil de pression.
  if (Array.isArray(eventYears) && eventYears.length) {
    const years = new Set(eventYears);
    serie.forEach((p, i) => {
      if (i && years.has(+p.date_mois.slice(0, 4)) && p.date_mois.slice(5, 7) === "06") {
        const tick = document.createElementNS(SVGNS, "line");
        tick.setAttribute("x1", String(x(i))); tick.setAttribute("x2", String(x(i)));
        tick.setAttribute("y1", "0"); tick.setAttribute("y2", String(H));
        tick.setAttribute("stroke", "#6d28d9"); tick.setAttribute("stroke-width", "1");
        tick.setAttribute("stroke-dasharray", "2 2"); tick.setAttribute("opacity", "0.55");
        svg.appendChild(tick);
      }
    });
  }
  const line = document.createElementNS(SVGNS, "polyline");
  line.setAttribute("fill", "none");
  line.setAttribute("stroke", cssVar("--risk-5"));
  line.setAttribute("stroke-width", "1.5");
  line.setAttribute("points", serie.map((p, i) => `${x(i)},${y(p.ip_rga_score || 0)}`).join(" "));
  svg.appendChild(line);
  const ai = serie.findIndex((p) => p.date_mois.slice(0, 7) === activeIso);
  if (ai >= 0) {
    const dot = document.createElementNS(SVGNS, "circle");
    dot.setAttribute("cx", String(x(ai)));
    dot.setAttribute("cy", String(y(serie[ai].ip_rga_score || 0)));
    dot.setAttribute("r", "3");
    dot.setAttribute("fill", cssVar("--ink"));
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

// Navigue le curseur de date vers un mois "AAAA-MM" (clic sur une case du calendrier de la fiche).
function goToMonth(ym) {
  const i = MONTHS.findIndex((m) => m.iso === ym);
  if (i >= 0) { stopPlay(); applyMonth(i, { refreshPanel: true }); }
}

// Calendrier de pression (B-plot) : grille mois × année, case colorée par niveau (même rampe que la
// carte) → la saisonnalité et les bandes de sécheresse (2022, 2017…) sautent aux yeux. Cliquer une
// case déplace le curseur de date sur ce mois. Construction DOM namespacée (pas d'innerHTML).
function pressionCalendar(serie, activeIso) {
  const wrap = elt("div", { class: "cal" });
  const byYM = {};
  let yMin = Infinity, yMax = -Infinity;
  for (const p of serie) {
    const ym = p.date_mois.slice(0, 7);
    byYM[ym] = p;
    const y = +ym.slice(0, 4);
    if (y < yMin) yMin = y;
    if (y > yMax) yMax = y;
  }
  if (yMax < yMin) return wrap;
  const activeYM = (activeIso || "").slice(0, 7);
  const grid = elt("div", { class: "cal-grid" });
  grid.append(elt("div", { class: "cal-corner" }));
  for (let m = 0; m < 12; m++) {
    grid.append(elt("div", { class: "cal-mh", text: FR_MONTHS[m][0].toUpperCase() }));
  }
  for (let y = yMin; y <= yMax; y++) {
    grid.append(elt("div", { class: "cal-yh", text: String(y) }));
    for (let m = 1; m <= 12; m++) {
      const ym = y + "-" + String(m).padStart(2, "0");
      const p = byYM[ym];
      const cell = elt("div", { class: "cal-cell" });
      if (!p) {
        cell.classList.add("empty");
      } else {
        const code = p.ip_rga_niveau_code || 0;
        cell.style.background = code ? NIVEAU_COLORS[code] : GREY;
        const lvl = p.ip_rga_niveau || niveauInfo(code).label;
        const sc = p.ip_rga_score == null ? "—" : p.ip_rga_score;
        cell.title = FR_MONTHS[m - 1] + " " + y + " · " + lvl + " · " + sc;
        if (ym === activeYM) cell.classList.add("active");
        cell.setAttribute("role", "button");
        cell.setAttribute("tabindex", "0");
        cell.setAttribute("aria-label", cell.title);
        cell.onclick = () => goToMonth(ym);
        cell.onkeydown = (e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); goToMonth(ym); }
        };
      }
      grid.append(cell);
    }
  }
  wrap.append(grid);
  wrap.append(elt("div", {
    class: "cal-cap", text: "Calendrier de pression — chaque case = un mois ; cliquez pour y aller.",
  }));
  return wrap;
}

// --- Comparateur de communes (B-plot) : épingle jusqu'à 4 communes et superpose leurs trajectoires
// de score (108 mois). Palette catégorielle (≠ rampe de pression), lisible en clair comme en sombre.
const CMP_COLORS = ["#0e7c86", "#b45309", "#6d28d9", "#be123c"];
const CMP_MAX = 4;
const cmpColor = (i) => CMP_COLORS[i % CMP_COLORS.length];

async function addToCompare(insee, nom) {
  if (!insee || compareSet.length >= CMP_MAX || compareSet.some((c) => c.insee === insee)) return;
  // Réutilise la série déjà chargée si c'est la commune ouverte, sinon un /serie (mis en cache).
  let serie = insee === openInsee && lastSerie ? lastSerie : null;
  if (!serie) {
    try {
      serie = (await (await fetch("/communes/" + encodeURIComponent(insee) + "/serie")).json()).serie;
    } catch (_) { serie = []; }
  }
  // Indice de couleur STABLE (le plus petit libre) → retirer une commune ne recolore pas les autres.
  const used = new Set(compareSet.map((c) => c.ci));
  let ci = 0;
  while (used.has(ci)) ci++;
  compareSet.push({ insee, nom: nom || insee, serie: serie || [], ci });
  renderFiche();
}
function removeFromCompare(insee) {
  compareSet = compareSet.filter((c) => c.insee !== insee);
  renderFiche();
}

// Graphe multi-lignes (SVG no-dep) : axe x = les 108 mois (référence MONTHS), y = score 0-100,
// repères 0/50/100, guide verticale du mois actif, 1 polyline colorée par commune (ouverte = épaisse).
function compareChart() {
  const wrap = elt("div", { class: "cmp-chart" });
  if (!compareSet.length || MONTHS.length < 2) return wrap;
  const n = MONTHS.length;
  const W = 316, H = 104, padL = 20, padR = 5, padT = 6, padB = 4;
  const x = (i) => padL + (i * (W - padL - padR)) / (n - 1);
  const y = (s) => H - padB - (Math.max(0, Math.min(100, s)) / 100) * (H - padT - padB);
  const svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("class", "cmp-svg");
  const hair = cssVar("--hairline") || "rgba(0,0,0,.1)";
  const muted = cssVar("--muted") || "#999";
  for (const g of [0, 50, 100]) {
    const gy = y(g);
    const ln = document.createElementNS(SVGNS, "line");
    ln.setAttribute("x1", String(padL)); ln.setAttribute("x2", String(W - padR));
    ln.setAttribute("y1", String(gy)); ln.setAttribute("y2", String(gy));
    ln.setAttribute("stroke", hair); ln.setAttribute("stroke-width", "1");
    svg.appendChild(ln);
    const t = document.createElementNS(SVGNS, "text");
    t.setAttribute("x", "0"); t.setAttribute("y", String(gy + 3));
    t.setAttribute("font-size", "8"); t.setAttribute("fill", muted);
    t.textContent = String(g);
    svg.appendChild(t);
  }
  // Guide verticale du mois actif (le même que le curseur de date).
  const ax = x(idx);
  const guide = document.createElementNS(SVGNS, "line");
  guide.setAttribute("x1", String(ax)); guide.setAttribute("x2", String(ax));
  guide.setAttribute("y1", String(padT)); guide.setAttribute("y2", String(H - padB));
  guide.setAttribute("stroke", cssVar("--ink") || "#333"); guide.setAttribute("stroke-width", "0.8");
  guide.setAttribute("stroke-dasharray", "2 2"); guide.setAttribute("opacity", "0.35");
  svg.appendChild(guide);
  for (const c of compareSet) {
    const byYM = {};
    for (const p of c.serie) if (p.ip_rga_score != null) byYM[p.date_mois.slice(0, 7)] = p.ip_rga_score;
    // Découpe en segments contigus : pas de trait droit trompeur par-dessus un trou de données.
    const runs = [];
    let run = null;
    MONTHS.forEach((m, i) => {
      const s = byYM[m.iso];
      if (s == null) { run = null; return; }
      if (!run) { run = []; runs.push(run); }
      run.push(x(i) + "," + y(s));
    });
    const thick = c.insee === openInsee;
    for (const pts of runs) {
      if (pts.length < 2) continue;
      const pl = document.createElementNS(SVGNS, "polyline");
      pl.setAttribute("fill", "none");
      pl.setAttribute("stroke", cmpColor(c.ci));
      pl.setAttribute("stroke-width", thick ? "2" : "1.3");
      pl.setAttribute("stroke-linejoin", "round");
      if (!thick) pl.setAttribute("opacity", "0.85");
      pl.setAttribute("points", pts.join(" "));
      svg.appendChild(pl);
    }
  }
  wrap.appendChild(svg);
  return wrap;
}

// Section « Comparateur » de la fiche : bouton épingler/retirer + graphe + légende (puces × supprimer).
function compareSection() {
  const sec = elt("div", { class: "cmp" });
  sec.append(elt("span", { class: "k", text: "Comparateur de communes" }));
  const inSet = compareSet.some((c) => c.insee === openInsee);
  const btn = elt("button", { class: "cmp-add" });
  if (inSet) {
    btn.textContent = "− Retirer du comparateur";
    btn.onclick = () => removeFromCompare(openInsee);
  } else if (compareSet.length >= CMP_MAX) {
    btn.textContent = "Comparateur plein (" + CMP_MAX + ")";
    btn.disabled = true;
  } else {
    btn.textContent = "+ Comparer cette commune";
    btn.onclick = () => addToCompare(openInsee, openNom);
  }
  sec.append(btn);
  if (compareSet.length) {
    sec.append(compareChart());
    const leg = elt("div", { class: "cmp-legend" });
    compareSet.forEach((c) => {
      const chip = elt("span", { class: "cmp-chip" });
      const dot = elt("span", { class: "cmp-dot" });
      dot.style.background = cmpColor(c.ci);
      chip.append(dot, elt("span", { class: "cmp-name", text: c.nom }));
      const rm = elt("button", { class: "cmp-x", text: "×" });
      rm.setAttribute("aria-label", "Retirer " + c.nom);
      rm.onclick = () => removeFromCompare(c.insee);
      chip.append(rm);
      leg.append(chip);
    });
    sec.append(leg);
  } else {
    sec.append(elt("div", {
      class: "cmp-hint",
      text: "Épinglez des communes pour superposer leurs trajectoires de pression (jusqu'à "
        + CMP_MAX + "). La sélection vous suit de fiche en fiche.",
    }));
  }
  return sec;
}

const panel = document.getElementById("panel");

function openPanel(props) {
  openInsee = props.insee;
  openNom = props.nom || props.insee;
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
  // Badge « Reclassée 2026 » : rendu dans renderFiche (données API authoritatives) → s'affiche
  // aussi quand on ouvre une commune via la recherche (l'index ne porte pas le flag bascule).
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

    // Badge « Reclassée 2026 » (flag API, donc présent quel que soit le chemin d'ouverture).
    if (truthy(f.basculement_2026)) {
      body.append(elt("span", { class: "badge bascule", text: "Reclassée 2026" }));
    }

    // Hero-number : gros score de pression (mono) + pastille du niveau + mois.
    const ni = niveauInfo(f.ip_rga_niveau_code);
    const hero = elt("div", { class: "hero" });
    const score = elt("div", { class: "score" });
    if (f.ip_rga_score == null) {
      score.textContent = "—";
    } else {
      score.textContent = String(f.ip_rga_score);
      score.append(elt("small", { text: " /100" }));
    }
    const heroMeta = elt("div", { class: "meta" });
    const pill = elt("span", { class: "e-pill", text: f.ip_rga_niveau || ni.label });
    pill.style.background = ni.color;
    pill.style.color = pillInk(f.ip_rga_niveau_code || 0);
    heroMeta.append(pill);
    if (mo) heroMeta.append(elt("span", { class: "mois", text: monthLabelOf(mo) }));
    hero.append(score, heroMeta);
    body.append(hero);

    // Sparkline (série en cache)
    if (!lastSerie) {
      try {
        lastSerie = (await (await fetch("/communes/" + encodeURIComponent(openInsee) + "/serie")).json()).serie;
      } catch (_) { lastSerie = []; }
    }
    // Sparkline + calendrier seulement si la commune a une vraie pression (≥1 mois niveau > 0) :
    // une commune hors couverture RGA (E=0, ex. Paris) n'affiche ni courbe plate ni grille grise,
    // le bandeau « hors couverture » + les KV suffisent.
    if (lastSerie && lastSerie.length > 1 && lastSerie.some((p) => (p.ip_rga_niveau_code || 0) > 0)) {
      body.append(sparkline(lastSerie, mo ? mo.iso : "", f.annees_reco));
      body.append(pressionCalendar(lastSerie, mo ? mo.iso : ""));
    }

    body.append(
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
    // Comparateur de communes (B-plot) : superpose les trajectoires des communes épinglées.
    body.append(compareSection());
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

// --- Recherche commune : index statique + MiniSearch (fuzzy, accents, CP, INSEE, autocomplete) ---
// Remplace l'ancien `querySourceFeatures` (ne voyait que les communes rendues + match exact).

// Repli d'accents + normalisation (st→saint) pour une recherche tolérante aux variantes.
function fold(s) {
  return (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase()
    .replace(/['’]/g, " ").replace(/-/g, " ")
    .replace(/\bst\b/g, "saint").replace(/\bste\b/g, "sainte")
    .replace(/\s+/g, " ").trim();
}

const qInput = document.getElementById("q");
const suggestBox = document.getElementById("suggest");
let SEARCH = null;          // instance MiniSearch (null si CDN indispo)
let DOCS = [];              // documents {insee,nom,dept,bbox,cp,niveau}
const inseeMap = new Map(); // INSEE → doc (lookup exact)
const cpMap = new Map();    // code postal → [docs] (lookup exact)
let suggestions = [];       // suggestions affichées
let activeIdx = -1;         // option survolée au clavier

async function initSearch() {
  if (typeof MiniSearch === "undefined") return; // CDN indispo → recherche désactivée proprement
  let indexData;
  try { indexData = await (await fetch("communes-index.json")).json(); } catch (_) { return; }
  CP_DATE = indexData.last_updated_cp;
  HIST = indexData.hist || {};
  fillIntroDates();
  updateLegendCount();
  const d = indexData.data;
  DOCS = d.insee.map((insee, i) => ({
    id: insee, insee, nom: d.nom[i], dept: d.dept[i], bbox: d.bbox[i],
    cp: d.cp[i] || [], niveau: d.niveau ? d.niveau[i] : 0,
  }));
  for (const doc of DOCS) {
    inseeMap.set(doc.insee, doc);
    for (const c of doc.cp) { if (!cpMap.has(c)) cpMap.set(c, []); cpMap.get(c).push(doc); }
  }
  SEARCH = new MiniSearch({
    fields: ["nom", "cp"],
    storeFields: ["insee"],
    extractField: (doc, f) => (f === "cp" ? (doc.cp || []).join(" ") : doc[f]),
    processTerm: (t) => fold(t) || null,
    searchOptions: { fuzzy: 0.2, prefix: true, boost: { nom: 3 } },
  });
  SEARCH.addAll(DOCS);
}
initSearch();

// Renvoie jusqu'à 8 communes : raccourcis exacts (INSEE puis CP) AVANT le fuzzy, puis MiniSearch.
function queryCommunes(q) {
  const out = [];
  const seen = new Set();
  const push = (doc) => { if (doc && !seen.has(doc.insee)) { seen.add(doc.insee); out.push(doc); } };
  const raw = q.trim();
  if (/^\d[\dab]\d{3}$/i.test(raw)) push(inseeMap.get(raw.toUpperCase())); // INSEE (Corse 2A/2B)
  if (/^\d{5}$/.test(raw)) for (const d of (cpMap.get(raw) || [])) push(d); // code postal exact
  // Saisie numérique (CP) : préfixe sans fuzzy (sinon "31000" ramène 21000/32000…).
  const opts = /^\d+$/.test(raw) ? { fuzzy: false, prefix: true } : undefined;
  if (SEARCH) for (const r of SEARCH.search(raw, opts)) push(inseeMap.get(r.insee));
  if (out.length < 8 && /^\d{2,4}$/.test(raw)) { // préfixe CP partiel (ex. "3100")
    for (const [cp, docs] of cpMap) if (cp.startsWith(raw)) for (const d of docs) push(d);
  }
  return out.slice(0, 8);
}

// CP à afficher dans la suggestion : celui qui matche la saisie si numérique, sinon le 1er.
function shownCp(doc, q) {
  if (/^\d{2,5}$/.test(q)) { const m = doc.cp.find((c) => c.startsWith(q)); if (m) return m; }
  return doc.cp[0];
}

function closeSuggest() {
  suggestBox.hidden = true;
  suggestBox.replaceChildren();
  suggestions = []; activeIdx = -1;
  qInput.setAttribute("aria-expanded", "false");
  qInput.removeAttribute("aria-activedescendant");
}

function renderSuggestions(list, q) {
  suggestions = list; activeIdx = -1;
  suggestBox.replaceChildren();
  if (!list.length) { closeSuggest(); return; }
  list.forEach((doc, i) => {
    const li = elt("li", { class: "sg-item", id: "sg-" + i });
    li.setAttribute("role", "option");
    const sw = elt("span", { class: "sg-sw" });
    sw.style.background = NIVEAU_COLORS[doc.niveau] || GREY; // pastille du niveau (dernier mois)
    li.append(sw, elt("span", { class: "sg-main", text: doc.nom }));
    const meta = " · " + doc.dept + (doc.cp.length ? " · " + shownCp(doc, q) : "");
    li.append(elt("span", { class: "sg-meta", text: meta }));
    li.addEventListener("mousedown", (e) => { e.preventDefault(); select(doc); }); // avant blur
    suggestBox.append(li);
  });
  suggestBox.hidden = false;
  qInput.setAttribute("aria-expanded", "true");
}

function select(doc) {
  if (!doc) return;
  qInput.value = doc.nom;
  closeSuggest();
  const b = doc.bbox;
  map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 60, maxZoom: 12 });
  openPanel({ insee: doc.insee, nom: doc.nom, code_dept: doc.dept });
}

function moveActive(delta) {
  const items = suggestBox.querySelectorAll(".sg-item");
  if (!items.length) return;
  if (activeIdx >= 0) items[activeIdx].classList.remove("active");
  // Depuis l'état initial (-1) : ↓ → premier, ↑ → dernier (sinon la formule sauterait le dernier).
  activeIdx =
    activeIdx < 0 ? (delta > 0 ? 0 : items.length - 1) : (activeIdx + delta + items.length) % items.length;
  const el = items[activeIdx];
  el.classList.add("active");
  qInput.setAttribute("aria-activedescendant", el.id);
  el.scrollIntoView({ block: "nearest" });
}

let searchDebounce;
qInput.addEventListener("input", () => {
  clearTimeout(searchDebounce);
  const q = qInput.value.trim();
  if (q.length < 2) { closeSuggest(); return; }
  searchDebounce = setTimeout(() => renderSuggestions(queryCommunes(q), q), 120);
});

qInput.addEventListener("keydown", (e) => {
  if (suggestBox.hidden) return;
  if (e.key === "ArrowDown") { e.preventDefault(); moveActive(1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); moveActive(-1); }
  else if (e.key === "Enter" && activeIdx >= 0) { e.preventDefault(); select(suggestions[activeIdx]); }
  else if (e.key === "Escape") { closeSuggest(); }
});

document.getElementById("search").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const q = qInput.value.trim();
  if (!q) return;
  const list = suggestions.length ? suggestions : queryCommunes(q);
  if (list.length) select(activeIdx >= 0 ? suggestions[activeIdx] : list[0]);
  else if (/^\d[\dab]\d{3}$/i.test(q)) openPanel({ insee: q.toUpperCase() }); // INSEE hors index
});

document.addEventListener("click", (e) => { if (!e.target.closest(".search")) closeSuggest(); });

// --- A5 : overlay d'explication (« landing ») ---
const introEl = document.getElementById("intro");
function frDate(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  return isNaN(d) ? null : d.toLocaleDateString("fr-FR", { day: "numeric", month: "long", year: "numeric" });
}
function setIntroDate(id, iso) {
  const el = document.getElementById(id);
  if (el) el.textContent = frDate(iso) || "—";
}
// Remplit les dates de fraîcheur des sources (appelée dès que /meta ou l'index sont chargés).
function fillIntroDates() {
  if (META) {
    setIntroDate("lu-rga", META.last_updated_rga);
    setIntroDate("lu-swi", META.last_updated_swi);
    setIntroDate("lu-ips", META.last_updated_ips);
    setIntroDate("lu-gaspar", META.last_updated_gaspar);
    setIntroDate("lu-ae", META.last_updated_admin_express);
    setIntroDate("lu-insee", META.last_updated_insee || META.last_updated_fideli);
    setIntroDate("lu-dvf", META.last_updated_dvf);
  }
  if (CP_DATE) setIntroDate("lu-cp", CP_DATE);
}
let introLastFocus = null;
function openIntro() {
  introLastFocus = document.activeElement; // pour restaurer le focus à la fermeture
  introEl.hidden = false;
  document.getElementById("introGo").focus(); // place le focus dans le dialogue
}
function closeIntro() {
  introEl.hidden = true;
  try { localStorage.setItem("solveille_intro_seen", "1"); } catch (_) { /* stockage indispo */ }
  if (introLastFocus && introLastFocus.focus) introLastFocus.focus();
}
document.getElementById("aboutBtn").addEventListener("click", openIntro);
document.getElementById("introGo").addEventListener("click", closeIntro);
introEl.addEventListener("click", (e) => { if (e.target === introEl) closeIntro(); }); // clic backdrop
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !introEl.hidden) closeIntro(); });
// Piège le focus dans le dialogue (aria-modal n'est pas suffisant à lui seul) : Tab/Shift-Tab cyclent.
introEl.addEventListener("keydown", (e) => {
  if (e.key !== "Tab") return;
  const f = introEl.querySelectorAll('button, [href], input, [tabindex]:not([tabindex="-1"])');
  if (!f.length) return;
  const first = f[0], last = f[f.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
});
// 1er chargement : afficher l'intro une fois (puis mémorisée).
try { if (!localStorage.getItem("solveille_intro_seen")) openIntro(); } catch (_) { openIntro(); }

// --- B3 : thème clair/sombre (tokens) + légende interactive ---
// Ré-applique l'état des couches communes après un setStyle (qui les recrée avec leurs valeurs par
// défaut) : visibilité 2D/3D selon `is3D`, puis couleur + relief du mois courant.
function applyViewState() {
  if (map.getLayer("communes-3d")) {
    map.setLayoutProperty("communes-3d", "visibility", is3D ? "visible" : "none");
  }
  if (map.getLayer("communes-fill")) {
    map.setLayoutProperty("communes-fill", "visibility", is3D ? "none" : "visible");
  }
  if (MONTHS.length) paintMonth(idx);
}
function applyTheme(theme) {
  const dark = theme === "dark";
  document.documentElement.dataset.theme = dark ? "dark" : "";
  try { localStorage.setItem("solveille_theme", dark ? "dark" : "light"); } catch (_) { /* indispo */ }
  readPalette(); // GREY (--risk-0) change avec le thème ; la rampe data 1→5 est constante
  if (MONTHS.length) {
    EXPRS = MONTHS.map((m) => colorExpr("n_" + m.key));
    BIV_EXPRS = MONTHS.map((m) => bivExpr("t_" + m.key)); // le neutre (GREY) suit le thème
  }
  // Le basemap vectoriel a sa propre palette clair/sombre (flavor Protomaps) → on reconstruit tout
  // le style, puis on ré-applique l'état dès que le nouveau style est parsé (couches recréées).
  map.setStyle(buildStyle(theme));
  map.once("styledata", applyViewState);
  // Les SVG de la fiche (sparkline, comparateur) figent leurs couleurs d'axe au rendu (cssVar) →
  // on re-rend la fiche ouverte pour qu'ils suivent le thème (séries en cache, pas de refetch lourd).
  if (openInsee) renderFiche();
}
document.getElementById("themeBtn").addEventListener("click", () => {
  applyTheme(themeNow() === "dark" ? "light" : "dark");
});

// --- B1 : vue 3D « montagnes de pression » (fill-extrusion, toggle 2D/3D + pitch). ---
function toggle3D() {
  is3D = !is3D;
  map.setLayoutProperty("communes-3d", "visibility", is3D ? "visible" : "none");
  map.setLayoutProperty("communes-fill", "visibility", is3D ? "none" : "visible");
  map.easeTo({ pitch: is3D ? 50 : 0, duration: 600 });
  const b = document.getElementById("d3Btn");
  b.classList.toggle("on", is3D);
  b.setAttribute("aria-pressed", String(is3D));
  if (MONTHS.length) paintMonth(idx); // applique couleur + hauteur du mois courant
}
document.getElementById("d3Btn").addEventListener("click", toggle3D);

// Légende : survol d'une classe → met en avant (atténue les autres segments).
const gradEl = document.getElementById("grad");
if (gradEl) {
  const segs = [...gradEl.querySelectorAll("span")];
  gradEl.addEventListener("mouseover", (e) => {
    const niv = e.target.dataset ? e.target.dataset.niv : null;
    segs.forEach((s) => s.classList.toggle("dim", !!niv && s.dataset.niv !== niv));
  });
  gradEl.addEventListener("mouseleave", () => segs.forEach((s) => s.classList.remove("dim")));
}

// --- B1 : carte bivariée Exposition × Tension (onglets de légende + matrice 3×3). ---
function fillBivGrid() {
  const g = document.getElementById("bivGrid");
  if (!g || g.childElementCount) return; // une seule fois
  // Rangées du haut (forte expo e=3) vers le bas (e=1) ; colonnes t=1→3 (gauche→droite).
  for (const e of [3, 2, 1]) {
    for (const t of [1, 2, 3]) {
      const cell = elt("span");
      cell.style.background = BIV[(e - 1) * 3 + t];
      cell.title = `exposition ${e}/3 · sécheresse ${t}/3`;
      g.append(cell);
    }
  }
}
function setMode(mode) {
  mapMode = mode === "biv" ? "biv" : "pression";
  const biv = mapMode === "biv";
  document.getElementById("modePression").hidden = biv;
  document.getElementById("modeBiv").hidden = !biv;
  document.getElementById("tabPression").classList.toggle("on", !biv);
  document.getElementById("tabBiv").classList.toggle("on", biv);
  document.getElementById("tabPression").setAttribute("aria-selected", String(!biv));
  document.getElementById("tabBiv").setAttribute("aria-selected", String(biv));
  document.getElementById("legendTitle").firstChild.textContent = biv
    ? "Exposition × Tension — "
    : "Pression RGA — ";
  if (biv) fillBivGrid();
  if (MONTHS.length) paintMonth(idx);
}
document.getElementById("tabPression").addEventListener("click", () => setMode("pression"));
document.getElementById("tabBiv").addEventListener("click", () => setMode("biv"));
