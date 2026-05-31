# 06 — START V3 (suite) · Reste de la Partie B (data-viz/perf/design avancés)

> **À coller comme premier message d'une nouvelle session Claude Code** (contexte propre).
> Contexte repo (`AGENTS.md`, `docs/`, code, commits) + ce fichier + la mémoire projet.
> Fait suite à `docs/05-START-V3.md` : la **Partie A** et **le gros de la Partie B** sont **livrés en prod**.

---

## Où on en est (TOUT ce qui suit est EN PROD sur https://argile.hectorb.fr)

Solveille est **en prod national**, CI/CD GitHub Actions, timers SWI mensuel / nappes quotidien / GASPAR
hebdo. La **Partie A** (bugs/UX) et **la majeure partie de la Partie B** (data-viz/design/perf) sont
déployées et vérifiées. État précis :

### Partie A (livrée) — voir `05-START-V3.md` §A
- **A1/A2** `tiles.py` : couverture complète au dézoom (plus de drop) + anti-slivers via **mapshaper**
  (simplif topologique, fallback gracieux). Zoom 4→9, `--no-tile-size-limit --no-feature-limit
  --no-tiny-polygon-reduction --no-simplification-of-shared-nodes --hilbert`, `SIMPLIFY_M=0`. PMTiles ~34 Mo.
- **A3** slider fluide (rAF + expressions pré-compilées). **A4** recherche **MiniSearch** (index statique
  `front/communes-index.json` + connecteur codes postaux La Poste). **A5** overlay « À propos » + sources datées.

### Partie B (livrée) — voir `05-START-V3.md` §B
- **B3 design** : tokens CSS **ocre→brique** (`--risk-0..5`, source UNIQUE UI+carte — `front/app.js`
  lit `--risk-*` via `getComputedStyle` → `NIVEAU_COLORS`/`colorExpr`), accent **teal** `#0e7c86`,
  **Inter + IBM Plex Mono self-hostés** (`front/fonts/*.woff2`, `@font-face`), **dark/light** (bouton ◐,
  persistant localStorage, swap basemap CARTO `light_all↔dark_all`), légende **barre de gradient**
  interactive + compteur « ≈ N communes en pression élevée+ ce mois » (histogramme mensuel précalculé
  dans `communes-index.json`), fiche **hero-number** (mono), favicon inline.
- **B1 data-viz** : **3D fill-extrusion** « montagnes de pression » (couche `communes-3d`, toggle 2D/3D,
  hauteur=niveau, pitch 50) ; **animation play** (boucle 108 mois, ~220 ms) ; **carte BIVARIÉE E×T**
  (matrice 3×3 Stevens diverging teal↔brique ; `tiles.py` produit `e_bin` statique + `t_AAAAMM` bin
  tension/mois ; onglets de légende Pression/E×T ; `BIV_EXPRS` pré-compilées) ; **marqueurs Cat-Nat**
  (tics violets `annees_reco`) sur le sparkline de la fiche.
- **B2 perf** : middleware **ETag (mtime des marts) + Cache-Control + 304** sur `/communes*` et `/meta`
  (`solveille/api/main.py`).
- **Rebuild complet prod** effectué : `/meta` a maintenant **toutes** les `last_updated_*` (le raw v0
  était absent de la VM → dates nulles ; corrigé par `make fetch-v0 && make build && make tiles`).

---

## RESTE À FAIRE (cette session) — 3 items du brief + finitions

> Ordre conseillé : **B-vec (basemap)** d'abord (le plus visible), puis **B-perf (immutable/MVT)**,
> puis **B-plot (Observable)** ; **deck.gl** en dernier (ambitieux). Tout est **client-side ou perf**,
> sauf si on veut un nouvel attribut de tuile (préciser en plan).

### B-vec — Basemap VECTORIEL clair + labels AU-DESSUS du choroplèthe + self-host glyphs
**Pourquoi** : aujourd'hui le fond est un **raster CARTO** (`light_all/dark_all`) sous le fill 0.78 → les
**noms de lieux sont noyés** sous le choroplèthe. Un basemap **vectoriel** permet de remettre les labels
de villes/communes **au-dessus** du fill (lecture pro, type Datawrapper/FT).
**Plan** :
- Style vectoriel clair **CARTO Positron** (`positron-gl-style`) ou **Protomaps** (basemap PMTiles +
  style `protomaps-themes-base`, CC-BY ; self-hostable). Garder une variante **sombre** pour le dark mode.
- **Self-host les glyphs** : le style actuel pointe `glyphs:"https://demotiles.maplibre.org/font/…"`
  (**fragile**). Générer/héberger des glyphs PBF (font-maker `fontnik`/`maplibre-font-maker`, ou
  reprendre les glyphs CARTO/Protomaps) sous `front/glyphs/{fontstack}/{range}.pbf` (gitignorer si gros)
  et pointer `glyphs:"glyphs/{fontstack}/{range}.pbf"`.
- **Ordre des couches** : insérer le `communes-fill` + `communes-line` SOUS les couches de **labels** du
  basemap (utiliser `map.addLayer(layer, beforeId)` avec le 1er layer symbol du basemap), opacité du fill
  ~0.7-0.78 pour laisser respirer les labels. Garder le `bg`/dark/light token.
- ⚠️ **Re-tester** : dark/light (le basemap vectoriel a sa propre palette → adapter aux tokens si possible),
  couverture (les labels ne doivent pas masquer le choroplèthe), perf (vecteur = plus de couches).
**Refs** : CARTO `gl-styles` GitHub, Protomaps `basemaps`/`protomaps-themes-base`, MapLibre `addLayer(beforeId)`.

### B-perf — pmtiles/index immutable+hash OU endpoint MVT z/x/y
**Constat actuel** : CF sert `/tiles/communes.pmtiles` en **`cf-cache-status: DYNAMIC`** (Range 206 OK,
**pas de stale**, MAIS **pas d'edge-cache** → chaque visiteur re-télécharge depuis l'origine = egress).
Le navigateur, lui, cache via l'ETag de StaticFiles. **Deux options** (au choix) :
- **(a) immutable + hash** : sortir `communes-<hash8>.pmtiles` + `communes-index-<hash8>.json` (hash du
  contenu), exposer les noms via `/meta` (ou un `front/assets.json`), `app.js` construit l'URL,
  `Cache-Control: public, max-age=31536000, immutable`. Nettoyer les anciens fichiers hashés au build.
  ⚠️ `tiles.py` (sortie `communes.pmtiles`) + `build_search.py` (sortie fixe) + `api/main.py` (StaticFiles)
  + `front/app.js` (URL pmtiles + fetch index) + `deploy/Caddyfile` à coordonner. Le contrat front change.
- **(b) endpoint MVT** : route FastAPI `/{z}/{x}/{y}.mvt` qui lit les ranges du `.pmtiles` local (lib
  `pmtiles` Python, index mmap, ~50 lignes) → **200 cacheables** par CF (tiered cache gratuit), sans Range.
  Plus robuste côté CF mais migre le contrat tuiles du front (source `vector` `tiles:[…/{z}/{x}/{y}.mvt]`).
- **Aussi** : `Cache Rule` Cloudflare « Eligible for cache » sur `/meta`/`/communes*` (sinon non cachés au
  edge faute d'extension) ; activer **Tiered Cache** (gratuit). Garder `X-Robots-Tag noindex`.
- (Optionnel) `functools.lru_cache` sur `fetch_commune`/`fetch_serie` (clé = mtime du mart) — gain marginal
  vs ETag, **attention aux dict mutables** (copier avant de muter, cf. `routes.get_meta`).

### B-plot — Panneaux Observable Plot dans la fiche (data-journalisme)
**Aujourd'hui** : sparkline SVG (no-dep) + marqueurs Cat-Nat. **À enrichir** (au choix, CDN Observable Plot
`@observablehq/plot` + d3, ou rester SVG no-dep si on veut éviter la dépendance — le projet self-host le
reste) : heatmap commune×108 mois, **aire empilée E·T**, distribution commune vs département (ridgeline),
et un **comparateur de communes** (épingler 2-4 communes, superposer leurs `/serie`). Reste **DOM-safe**.

### deck.gl (ambitieux, v3.x) — 3D animée GPU
`MapboxOverlay({interleaved:true})` + `GeoArrowSolidPolygonLayer` (triangulation multi-thread, transitions
GPU couleur/élévation entre mois). **À réserver à la 3D animée** (un bench montre MapLibre ~2× plus rapide
en PMTiles 2D → **ne pas** remplacer le rendu 2D existant). Précompute GeoParquet/Arrow des géométries +
matrice scores. **Gros chantier** — à cadrer en plan d'abord.

---

## Procédure de DÉPLOIEMENT (éprouvée cette session — la reproduire)

1. Travailler sur une **branche** `feat/v3-…` (commits atomiques, `make lint` + `make test` verts).
2. **Merge → `main` + push** : la CI (`.github/workflows/ci.yml`) lint+types+tests puis **déploie le code**
   par SSH (`git reset --hard origin/main` + `uv sync` + restart `solveille-api`). Surveiller : `gh run watch`.
3. **Sur la VM** (`root@178.104.144.205`, accès autorisé par Hector) — selon ce qui a changé :
   - Nouveau **schéma de tuiles** (nouvel attribut) ou nouvel **index** → `cd /opt/solveille &&
     export PATH=/usr/local/bin:$HOME/.local/bin:$PATH && make tiles` (≈1 min, pic ~1,25 Go, OK 8 Go ;
     régénère pmtiles **+** `front/communes-index.json`). `mapshaper-xl` est installé (`/usr/bin`).
   - **Données** v0/dynamiques → `make fetch-* && make build` (rebuild complet national : **sans OOM**,
     ~15-20 min ; RGA per-dept, piézo batché). Rebuild complet déjà fait le 31-05.
   - Changement **`deploy/Caddyfile`** → `systemctl reload caddy` (la CI ne recharge PAS Caddy).
4. **Vérifier la prod en contexte navigateur FRAIS** (chrome-devtools, contexte isolé) : couverture,
   recherche, dark/light, 3D, bivarié, ETag/304, dates `/meta`.

---

## Pièges à NE PAS redécouvrir (hérités A + B)

- **`make lint` avant push** : CI = `ruff check` ET `ruff format --check` ET `mypy` (ligne **≤ 100**).
- **Tuiles** : `mapshaper` requis sur la VM (sinon A2 dégrade en silence, log `mapshaper=False`). Les
  GeoJSON intermédiaires (377 Mo) sont **supprimés** après build (seul le `.pmtiles` reste dans `tiles/out`).
- **Artefacts générés gitignorés** : `tiles/out/`, `front/communes-index.json` (régénérés par `make tiles`
  sur la VM ; survivent au `git reset --hard`). Ne pas les committer.
- **⚠️ NE PAS compresser le `.pmtiles`** (Caddy `encode` casse les Range → 200 fichier entier). Vérifié OK
  actuellement (CF `DYNAMIC`, Range 206). Si on touche au serving (B-perf), re-vérifier
  `curl -I -H 'Range: bytes=0-99' …pmtiles` → **206**.
- **API tolérante au schéma** (`api/deps.py`) : toute nouvelle colonne de mart lue **défensivement**.
- **Codes postaux** (si on y retouche) : CSV **Latin-1** (pas UTF-8), en-tête préfixée `#`, **PLM** = la base
  n'a que les arrondissements (75101-75120/69381-69389/13201-13216) → **rollup** vers commune COG.
- **Couleurs = tokens** : ne PAS re-hardcoder d'hex ; ajouter/lire des CSS vars (`--risk-*`, `--accent`…).
  `app.js` rebuilde `EXPRS`/`BIV_EXPRS` au changement de thème (le neutre `--risk-0` change).
- **Anti-OOM VM 8 Go** : builds bornés (par-dept / batch). `make tiles` ≈ 1,25 Go, `make build` full ≈ 2,4 Go.
- **Cloudflare** : front (`/ /index.html /app.js /communes-index.json`) en `no-cache` (Caddyfile) →
  propagation immédiate ; le pmtiles est `DYNAMIC`.
- **Mémoire projet** : `solveille-v0-done-v1-next.md` retrace tout l'historique (A + B + rebuild).

## Garde-fous (inchangés)
EPSG:2154 ; sources **tracées** (`last_updated_*`) ; indice **indicatif** (jamais un diagnostic) ; DVF
agrégats communaux + `noindex` ; **commits atomiques** ; **rien de destructif / push / op VM sans accord**
explicite ; politesse réseau (cache + bornage).
