// Solveille — 3D animée GPU (deck.gl), « montagnes de pression ». Chargé en <script> AVANT app.js.
// Expose `window.Deck3D`. Le bundle deck.gl UMD (~1,5 Mo) et les données binaires sont chargés
// PARESSEUSEMENT à la 1ʳᵉ activation 3D → les visiteurs 2D ne paient rien. Si le chargement échoue
// (réseau, etc.), `enable()` est un no-op silencieux → app.js garde la 3D `fill-extrusion` MapLibre.
//
// Élévation CONTINUE = (score/maxScore)^γ · maxHeightM (≠ 5 marches MapLibre) ; couleur = niveau via
// les seuils (mêmes tokens CSS `--risk-*` que la carte). Géométrie binaire STATIQUE (positions +
// startIndices par partie) ; seules l'élévation et la couleur sont des accesseurs indexés sur le mois
// → deck interpole couleur+hauteur sur le GPU (`transitions`) au changement de mois.
"use strict";

window.Deck3D = (() => {
  const DECK_SRC = "vendor/deck.gl-9.3.2.min.js";
  const JSON_URL = location.origin + "/tiles/communes-3d.json";
  const BIN_URL = location.origin + "/tiles/communes-3d.bin";
  const LAYER_ID = "communes-3d-gpu";

  let map = null;
  let overlay = null; // deck.MapboxOverlay (créé une fois, conservé chaud)
  let active = false; // couche deck actuellement posée
  let wantActive = false; // intention (3D demandée) — gère un disable pendant le chargement
  let loadingPromise = null; // chargement deck UMD + data, une seule fois
  let scriptInjected = false; // bundle UMD déjà injecté (évite de ré-injecter <script> sur retry)
  let DATA = null; // vues typées sur l'artefact binaire
  let palette = null; // [GREY, c1..c5] en [r,g,b], lus des tokens CSS
  let curMonth = 0;
  let onReadyCb = null; // appelé quand deck prend la main (app.js masque alors la 3D MapLibre)

  const themeTag = () => document.documentElement.dataset.theme || "light";

  function hexToRgb(hex) {
    const h = (hex || "").replace("#", "").trim();
    const full = h.length === 3 ? h.replace(/./g, (c) => c + c) : h;
    const n = parseInt(full || "888888", 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function buildPalette() {
    const cv = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    palette = [0, 1, 2, 3, 4, 5].map((i) => hexToRgb(cv("--risk-" + i)));
  }

  function injectScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src;
      s.async = true;
      s.onload = () => resolve();
      s.onerror = () => reject(new Error("deck.gl UMD introuvable (" + src + ")"));
      document.head.appendChild(s);
    });
  }

  function ensureLoaded() {
    if (loadingPromise) return loadingPromise;
    loadingPromise = (async () => {
      if (!scriptInjected) {
        await injectScript(DECK_SRC); // un seul <script>, même si un fetch data échoue ensuite
        scriptInjected = true;
      }
      if (!window.deck || !window.deck.MapboxOverlay || !window.deck.SolidPolygonLayer) {
        throw new Error("deck.gl chargé mais MapboxOverlay/SolidPolygonLayer absents");
      }
      const [hdr, buf] = await Promise.all([
        fetch(JSON_URL).then((r) => {
          if (!r.ok) throw new Error("communes-3d.json " + r.status);
          return r.json();
        }),
        fetch(BIN_URL).then((r) => {
          if (!r.ok) throw new Error("communes-3d.bin " + r.status);
          return r.arrayBuffer();
        }),
      ]);
      const L = hdr.layout;
      DATA = {
        hdr,
        POS: new Float32Array(buf, L.positions.offset, L.positions.count),
        PSTART: new Uint32Array(buf, L.partStarts.offset, L.partStarts.count),
        PCOMM: new Uint32Array(buf, L.partCommune.offset, L.partCommune.count),
        SCORES: new Uint8Array(buf, L.scores.offset, L.scores.count),
        HASCLAY: new Uint8Array(buf, L.hasClay.offset, L.hasClay.count),
        nMonths: hdr.nMonths,
        nParts: L.partCommune.count,
        elev: hdr.elevation,
        seuils: hdr.seuils,
      };
      buildPalette();
    })().catch((err) => {
      loadingPromise = null; // permet un nouvel essai à la prochaine activation
      throw err;
    });
    return loadingPromise;
  }

  // Niveau 1-5 d'un score (mêmes seuils que le mart → couleur identique à la 2D).
  function niveauOf(score) {
    const s = DATA.seuils;
    return score <= s[0] ? 1 : score <= s[1] ? 2 : score <= s[2] ? 3 : score <= s[3] ? 4 : 5;
  }
  function scoreOfPart(index) {
    return DATA.SCORES[DATA.PCOMM[index] * DATA.nMonths + curMonth];
  }
  function colorOfPart(index) {
    // Hors couverture argile (E=0) → gris, comme la 2D (sans ça, score 0 deviendrait niveau 1).
    if (!DATA.HASCLAY[DATA.PCOMM[index]]) return palette[0];
    return palette[niveauOf(scoreOfPart(index))];
  }
  function elevOfPart(index) {
    const e = DATA.elev;
    return Math.pow(scoreOfPart(index) / e.maxScore, e.gamma) * e.maxHeightM;
  }

  function firstSymbolId() {
    const layers = (map.getStyle().layers || []);
    const sym = layers.find((l) => l.type === "symbol");
    return sym ? sym.id : undefined;
  }

  function makeLayer() {
    const D = window.deck;
    return new D.SolidPolygonLayer({
      id: LAYER_ID,
      // Géométrie binaire statique : 1 « objet » par partie de polygone (startIndices), les accesseurs
      // d'élévation/couleur sont appelés par partie et répliqués sur ses sommets par deck.
      data: {
        length: DATA.nParts,
        startIndices: DATA.PSTART,
        attributes: { getPolygon: { value: DATA.POS, size: 2 } },
      },
      _normalize: false,
      extruded: true,
      pickable: false, // picking via MapLibre (clic→fiche) — inchangé vs 2D ; deck-natif = Phase 2
      getElevation: (_, info) => elevOfPart(info.index),
      getFillColor: (_, info) => colorOfPart(info.index),
      updateTriggers: { getElevation: curMonth, getFillColor: [curMonth, themeTag()] },
      transitions: { getElevation: 450, getFillColor: { duration: 300, easing: (t) => t * (2 - t) } },
      opacity: 0.92,
      beforeId: firstSymbolId(), // sous les labels du basemap (lecture pro, cohérent avec la 2D)
    });
  }

  function _apply(layers) {
    if (!overlay) {
      overlay = new window.deck.MapboxOverlay({ interleaved: true, layers });
      map.addControl(overlay);
    } else {
      overlay.setProps({ layers });
    }
  }

  return {
    /** Mémorise l'instance MapLibre (appelé une fois par app.js après création de la carte). */
    init(mapInstance) {
      map = mapInstance;
    },
    isActive() {
      return active;
    },
    /** Active la 3D GPU pour le mois `idx` ; `onReady` est appelé quand deck prend la main (app.js
     * masque alors la 3D MapLibre). No-op silencieux si le chargement échoue (repli MapLibre). */
    enable(idx, onReady) {
      wantActive = true;
      curMonth = idx;
      onReadyCb = onReady || null;
      if (active) {
        // Déjà actif (re-toggle rapide après chargement) : on repose juste + on notifie une fois.
        _apply([makeLayer()]);
        if (onReadyCb) {
          onReadyCb();
          onReadyCb = null;
        }
        return;
      }
      ensureLoaded()
        .then(() => {
          if (!wantActive || active) return; // désactivée pendant le chargement, ou déjà posée
          _apply([makeLayer()]);
          active = true;
          if (onReadyCb) {
            onReadyCb();
            onReadyCb = null; // ne pas re-déclencher via un enable concurrent
          }
        })
        .catch(() => {
          /* repli : app.js garde la 3D fill-extrusion MapLibre */
        });
    },
    disable() {
      wantActive = false;
      active = false;
      if (overlay) overlay.setProps({ layers: [] });
    },
    setMonth(idx) {
      curMonth = idx;
      if (active && overlay) _apply([makeLayer()]);
    },
    /** Thème changé / setStyle : recalcule la palette (tokens CSS) et repose la couche interleaved
     * (détruite par le setStyle), avec le bon `beforeId` du nouveau style. */
    refreshPalette() {
      if (DATA) buildPalette();
      if (active && overlay) _apply([makeLayer()]);
    },
  };
})();
