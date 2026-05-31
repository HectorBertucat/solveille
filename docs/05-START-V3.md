# 05 — START V3 · Polish UX (bugs carte + recherche + landing) + Évolution data-viz/design/perf

> **À coller comme premier message d'une nouvelle session Claude Code** (contexte propre).
> Contexte repo (`AGENTS.md`, `docs/`, code, commits) + ce fichier + la mémoire projet.
> Ce brief = synthèse d'un diagnostic code + d'une recherche (5 agents) réalisés en fin de v2.

---

## Où on en est (v0 + v1 + v2 — TOUS LIVRÉS ✅, EN PROD national)

Solveille est **en prod** sur <https://argile.hectorb.fr> (CI/CD GitHub Actions ; timers SWI mensuel,
nappes quotidien, GASPAR hebdo). **111 tests.** Les 4 composantes de l'indice tournent : **E** (expo
argile), **T** (tension SWI/nappes), **J** (valeur bâti exposé), **H** (calibration Cat-Nat sécheresse).
Carte choroplèthe MapLibre + PMTiles statique + curseur de date (108 mois) ; fiche commune en panneau.
Détails v2 : `docs/04-START-V2.md`, `docs/metric.md §H`, ADR-019.

**But de la v3** : rendre l'app **fluide**, **plus originale** (data-viz, éventuellement 3D),
**professionnelle** (anti « look IA générique »), et **expliquée** (landing). Le précalcul est OK si
ça aide les perfs. **MCP reporté** (pas prioritaire).

## Pièges à NE PAS redécouvrir (hérités v1.1/v2 — voir `04-START-V2.md`)
- **Anti-OOM VM 8 Go** : builds par lots + DuckDB borné. Le build national H tourne en ~3 s / 2,4 Go.
- **API tolérante au schéma** : toute nouvelle colonne de mart lue **défensivement** (`api/deps.py`).
- **`make lint`** avant push (CI = `ruff check` ET `ruff format --check` ET `mypy`).
- **Changement de schéma mart** ⇒ rebuild sur la VM (`fetch-*` + `systemctl start solveille-*.service`).
- **Cloudflare cache** : sans `Cache-Control` à l'origine, CF cache le statique ~4 h. Fix déjà posé
  pour l'app shell (`deploy/Caddyfile` `no-cache` sur `/ /index.html /app.js`) ; recharger Caddy à la
  main après changement (`systemctl reload caddy`, la CI ne le fait pas). **⚠️ NE PAS compresser le
  `.pmtiles`** (casse les Range → CF renvoie le fichier entier) — cf. Partie B/perf.
- SSH : `root@178.104.144.205` (Hector autorise lecture + rebuild + déploiement).

---

# PARTIE A — Bugs & UX (corriger d'abord)

> Tous **planifiables**, aucun trivial. Ordre conseillé : A1+A2 (tuiles, même build) → A3 (slider) →
> A4 (recherche) → A5 (landing). A1-A3 = quick wins à fort impact ressenti.

## A1 — Communes qui DISPARAISSENT au dézoom (trous blancs) · `solveille/transform/tiles.py`
**Cause** (confirmée) : les flags `--drop-densest-as-needed` + `--coalesce-smallest-as-needed`
(tiles.py `build_tiles` ~l.136-138) **droppent/fusionnent** des communes quand une tuile bas-zoom
dépasse la limite par défaut (500 K compressés / 200 000 features). Au dézoom, 34 746 communes se
concentrent dans peu de tuiles → dépassement → trous.
**Fix** : retirer ces 2 flags ; lever les limites (`--no-tile-size-limit` `--no-feature-limit`) ;
ordre spatial `--hilbert` ; `--no-tiny-polygon-reduction` (anti « polygon dust ») ; **min-zoom 4-5**
(pas 3 : la métropole tient à ~z5, des tuiles z3 surchargées = RAM inutile) ; **max-zoom 9-10** (à z9
une commune fait des centaines de px ; z11 = ×4 RAM/taille pour rien).

## A2 — ESPACES BLANCS (slivers) entre communes adjacentes · `tiles.py`
**Cause** : **double simplification non-topologique** — `ST_SimplifyPreserveTopology(geom, 75 m)` en
DuckDB (par polygone, ignore les voisins → chaque commune simplifie son bord partagé différemment)
**puis** `--simplification=10` dans tippecanoe.
**Fix recommandé (qualité carto, ~1-2 h)** : pré-simplifier la **topologie** avec **mapshaper** entre
DuckDB et tippecanoe, et **supprimer le simplify DuckDB** (`SIMPLIFY_M`→0, garder `ST_MakeValid`,
exporter en **L93 non simplifié**, mapshaper reprojette en WGS84 en fin de chaîne) :
```bash
mapshaper communes_l93.geojson \
  -clean gap-fill-area=0 \
  -simplify 8% keep-shapes planar \      # Visvalingam, topologie partagée préservée, garde les petites communes
  -proj wgs84 \
  -o precision=0.000001 format=geojson communes.geojson
```
mapshaper garde chaque bord partagé comme **un seul arc** → simplifié une fois → **zéro gap/overlap**.
*(Dépendance Node `mapshaper` à installer/épingler sur la VM : `npm i -g mapshaper@<ver>`.)*

**Commande tippecanoe finale** (A1+A2 ensemble ; apt 2.49 d'Ubuntu Noble supporte déjà tous ces flags) :
```bash
tippecanoe -o tiles/out/communes.pmtiles -l communes \
  -Z4 -z9 \
  --no-simplification-of-shared-nodes \   # -pn : bords partagés cohérents (REMPLACE --detect-shared-borders, DÉPRÉCIÉ)
  --no-tiny-polygon-reduction \           # -pt
  --coalesce \                            # -ac : fusion NON destructive d'attrs identiques
  --no-tile-size-limit --no-feature-limit \  # -pk -pf : ne droppe plus → couverture complète
  --hilbert \                             # -ah
  --simplification=1 \                    # géométrie déjà simplifiée par mapshaper
  --force tiles/out/communes.geojson
```
**Quick win sans mapshaper** (si on veut juste débugger vite) : garder le pipeline actuel mais mettre
`SIMPLIFY_M=0.0` côté DuckDB et laisser `-pn` + `--simplification=4` gérer dans tippecanoe (qualité un
cran en dessous de mapshaper mais sans nouvelle dépendance).
**Vérif (test, pas à l'œil)** : décoder une tuile z5 et compter les INSEE uniques (≈ 34 746) ; ajouter
un test pytest qui échoue si < 34 746. Build : PMTiles ~15-40 Mo, RAM < 3 Go (OK 8 Go).
**Refs** : tippecanoe README (`-pn`, `-ab` deprecated) ; felt.com/blog/tippecanoe-polygons-shard-gaps ;
mapshaper.org/docs/reference.html.

## A3 — Slider PAS « smooth » · `front/app.js`
**Cause** (confirmée, ce n'est PAS le GPU) : (1) l'event `input` non throttlé recolore 34 k communes
**plusieurs fois par frame** ; (2) chaque tick reconstruit une **nouvelle expression** `colorExpr` que
MapLibre re-parse. (MapLibre ne re-tessellate pas : il ré-évalue l'expression par feature + ré-upload
le buffer de couleurs.)
**Fix (quick win, ~20 lignes)** :
- Pré-compiler les 108 expressions **une fois** après `/meta` : `const EXPRS = MONTHS.map(m => colorExpr('n_'+m.key))`.
- Throttle via `requestAnimationFrame` (1 recolor max/frame) : `input` → `scheduleApply(idx)` (stocke
  `pendingIdx`, `requestAnimationFrame` si pas déjà en vol) ; `change` → applique + `renderFiche`.
- `colorExpr` **sans `coalesce`** (le pivot écrit déjà 0 si NULL) : `['match',['get',k],1,c1,…,GREY]`.
- Option : `fill-color-transition: { duration: 120 }` pour lisser le saut de mois.
- **NE PAS** utiliser `feature-state` (clés non purgées → lag au zoom, MapLibre #6633) ni migrer
  deck.gl juste pour la fluidité.

## A4 — Recherche commune intelligente (fuzzy + accents + CP + autocomplete) · `front/` + nouveau connecteur
**Cause** : le handler de recherche fait `map.querySourceFeatures` → ne voit que les communes
**rendues à l'écran** + match **exact**. À remplacer entièrement.
**Fix recommandé (client-side, ~1-2 h)** : index **statique** de 34 746 communes généré au build +
**MiniSearch** (CDN UMD ~7 Ko, zéro dép, index inversé + fuzzy + prefix — *pas Fuse.js*, O(n)/frappe).
- **Build** : nouvelle fonction (dans `tiles.py` ou `build_search.py`, appelée par `make build/tiles`)
  qui écrit `front/communes-index.<hash>.json` (format colonnaire, ~480-520 Ko gzip avec CP+centroïde).
  Centroïde déjà dérivable : `ST_X/Y(ST_Transform(ST_Centroid(ST_GeomFromWKB(geom_wkb)),'EPSG:2154','EPSG:4326',always_xy:=true))`
  sur `data/staging/commune.parquet` (testé : 01001 → 4.92585, 46.15372). Arrondir à 4 décimales.
- **Code postal** (absent du mart) : nouveau connecteur `opendata-connector` (idempotent, cache) →
  base data.gouv **« Base officielle des codes postaux »** (La Poste, **Licence Ouverte 2.0**,
  `https://www.data.gouv.fr/api/1/datasets/r/008a2dda-2c60-4b63-b910-998f6f818089`) *ou* « Communes de
  France — base des codes postaux » (ODbL, contient aussi lat/lon). **1 commune → N CP** (PLM,
  grandes villes) → stocker `cp[]` multi-valeur. Tracer `last_updated_cp` (garde-fou sources tracées).
- **Front** : `processTerm` accent-fold (`NFD` + strip diacritiques + `st`→`saint`), `searchOptions:
  { fuzzy:0.2, prefix:true, boost:{nom:3} }`. UX = `<input>` + listbox flottant (debounce ~120 ms,
  nav clavier ↑/↓/Enter/Esc, `aria-activedescendant`), suggestion = nom + dept + CP + **pastille
  couleur du niveau du mois courant** (mini-dataviz). `^\d{5}$`→CP, `^\d[\dAB]\d{3}$`→INSEE avant fuzzy.
  Sur sélection : `fitBounds`(centroïde±bbox) + `openPanel`.
- Cache : `Cache-Control immutable` + hash dans le nom (servi par Caddy/StaticFiles, gzip auto).

## A5 — Landing / page d'explication · `front/`
**Manque** : aucune explication de ce qu'est l'app. **Ajouter** une intro (overlay au 1er chargement,
section repliable, ou page séparée `/a-propos`) : **qu'est-ce que c'est** (nowcast communal de la
pression sécheresse-argiles), **pourquoi** (RGA = 1er poste d'indemnisation Cat-Nat, zonage 2026 aux
ventes), **comment lire** (E·T → score, niveaux, curseur de date, H), **sources** (BRGM/Géorisques,
Météo-France SWI, Hub'eau/ADES nappes, GASPAR/DGPR, IGN, Insee, SDES, DGFiP/DVF) avec dates `last_updated_*`,
**cadrage** « indicatif, pas un diagnostic ». Style éditorial (cf. Partie B/design). Garder `noindex`.

---

# PARTIE B — Évolution data-viz / design / perf (recherche → à prioriser avec Hector)

> Findings consolidés de la recherche. Présenter à Hector ; choisir le périmètre. Quick wins d'abord.

## B1 — Visualisation plus originale / « data-science »
- **Quick win — 3D `fill-extrusion` MapLibre natif** (1-2 h, **zéro changement pipeline** : les tuiles
  ont déjà `n_AAAAMM`). Couche `fill-extrusion` hauteur = niveau (ex. 1→4 km … 5→38 km), couleur = même
  rampe, `setPitch(50)` + toggle 2D/3D. Relief « montagnes de pression ». *(opacité ~0.85, hauteurs
  modérées pour l'occlusion ; fallback 2D mobile.)*
- **Quick win — Animation temporelle « play »** : bouton lecture qui boucle les 108 mois en `rAF`
  (~200 ms/mois) → « film de la sécheresse 2017→2025 » (pic 2022 parlant). Lissé = précalculer
  `s_AAAAMM` (score 0-100/mois) dans les tuiles + `interpolate` continu (PMTiles 40→~70-90 Mo, OK CDN).
- **Idée data-science forte — Carte BIVARIÉE Exposition × Tension** (matrice 3×3, palette Stevens) :
  montre « forte expo + sol encore humide » (latent) vs « forte expo + tension forte » (danger actif)
  vs « tension forte sans argile » (neutralisé). Nécessite `t_AAAAMM` (bin tension 1-3) + `e_bin` dans
  les tuiles ; légende grille 3×3 cliquable.
- **Panneaux liés Observable Plot** dans la fiche (CDN, pas de build, SVG, reste DOM-safe) : heatmap
  commune×108 mois, aire empilée E·T + marqueurs arrêtés Cat-Nat (`annees_reco`), distribution
  commune vs département (ridgeline). Remplace/enrichit le sparkline. + **comparateur de communes**
  (épingler 2-4, superposer `/serie`).
- **Ambitieux (v3.x) — deck.gl** `MapboxOverlay({interleaved:true})` + `GeoArrowSolidPolygonLayer`
  (triangulation multi-thread, transitions GPU couleur/élévation entre mois). **À réserver à la 3D
  animée** : un benchmark montre MapLibre ~2× plus rapide que deck.gl en PMTiles 2D → ne pas remplacer
  le rendu existant. Précompute : GeoParquet/Arrow des géométries + matrice scores.
- **Option hero** : globe MapLibre (`setProjection({type:'globe'})`, 1 ligne) pour la landing ;
  scrollytelling (scrollama, ~3 Ko) 4-5 chapitres (été 2022, communes reclassées 2026, top valeur).
- **À éviter** : kepler.gl (lourd, générique), H3/hexbin (détruit la maille communale porteuse de sens).

## B2 — Performance & caching
- **Slider** : cf. A3 (rAF + pré-compilation) = le vrai levier fluidité.
- **⚠️ Cloudflare/Caddy — NE PAS compresser le `.pmtiles`** : `encode zstd gzip` (Caddyfile l.6)
  s'applique à TOUT → casse les **Range requests** (Content-Length altéré → CF/navigateur retombent
  en `200` fichier entier 42 Mo au lieu de `206`). **Restreindre `encode` aux types texte**
  (`text/*`, `application/json`, `application/javascript`, `image/svg+xml`) OU exclure `/tiles`. Le
  tile-data PMTiles est déjà gzippé en interne. *(Vérif : `curl -H 'Range: bytes=0-99' …pmtiles` doit
  renvoyer 206.)*
- **PMTiles immutable + versionné** : sortir `communes-<hash8>.pmtiles`, nom exposé via `/meta`,
  `app.js` construit l'URL → `Cache-Control: public, max-age=31536000, immutable` + `Accept-Ranges`.
  Plus de purge CF, le navigateur garde le fichier.
- **CF cache des 206** peu fiable (Cache Reserve ne gère pas l'Origin Range) → activer **Tiered Cache**
  (gratuit) ; OU **alternative recommandée** : servir des tuiles `z/x/y.mvt` (200 cacheables, tiered)
  via un endpoint FastAPI lisant les ranges du `.pmtiles` local (lib `pmtiles` Python, ~50 lignes,
  index mmap) → CF efficace **sans** Range. (Dette de migration du contrat front.)
- **API FastAPI** : `/meta` (~1×/j), `/communes?insee&mois`, `/serie` immuables pour un mois donné →
  middleware `Cache-Control: public, max-age=… , stale-while-revalidate` + **ETag** ; **Cache Rule CF**
  « Eligible for cache » sur ces paths (sans extension, sinon non cachés). Garder `X-Robots-Tag noindex`
  (DVF). `functools.lru_cache(2048)` sur `fetch_commune`/`fetch_serie` (invalider via `mtime` du mart).
- **Précalcul** : `meta.json` statique au build (mois min/max, last_updated, nom pmtiles hashé) → 0
  requête DuckDB au boot. Le pivot mensuel dans la tuile = déjà le bon précalcul.
- **Mesure (preuve portfolio)** : `performance_start_trace` (chrome-devtools) en scrubbant AVANT/APRÈS
  le throttle (Long Tasks Recalculate Style/Paint).

## B3 — Direction design (pro, anti « look IA »)
- **Couleur** : garder une **séquentielle** (le risque va de rien→fort, pas un écart signé). Remplacer
  le YlOrRd brut par une rampe **ocre→brique** ancrée « argile/sécheresse », luminance régulière,
  daltonien-safe : `--risk-1:#fde8c4 · 2:#f9c178 · 3:#f08c3a · 4:#d6562a · 5:#9b2226`, neutre
  « papier » `#e7e3dc`. **Tokeniser en CSS vars** (source unique UI+carte ; aujourd'hui les hex sont
  dupliqués paint/`NIVEAU_COLORS`/légende → générer l'expression MapLibre depuis les tokens). Accent de
  marque **hors rampe data** : teal `#0e7c86` (abandonner l'ambre `#f59e0b` « Tailwind warning »).
  Garder le violet catégoriel `#6d28d9` (basculement 2026). *(Le divergent BrBG de l'IPS est correct —
  l'IPS EST un écart bas↔haut.)*
- **Typo** : self-host **Inter** (variable woff2, `tnum`) pour l'UI + **IBM Plex Mono** (ou Inter
  tabular-nums) pour tous les chiffres (scores, €, %, années, axes) = patte data-journalisme. Échelle
  ratio 1.2 (hero 44 / h1 28 / h2 20 / corps 15 / label 13 / note 11). Remplacer le stack système.
- **Layout** : passer à un **basemap VECTORIEL clair** (Protomaps light / CARTO Positron) au lieu du
  raster sous le fill → permet de mettre les **labels de lieux AU-DESSUS** du choroplèthe (aujourd'hui
  noyés sous le fill 0.78). **Self-host les glyphs** (le code pointe `demotiles.maplibre.org`, fragile).
  Légende = **barre de gradient** interactive (survol classe → highlight) + compteur « ≈ N communes en
  pression élevée+ ce mois ». Panneau fiche autour d'un **hero-number** (score 44px mono + pastille),
  sparkline pleine largeur, KV 2 colonnes, blocs E/T/H cardés. « Less chrome » : ombres légères, bordures
  hairline, radius 6-8px, header plus fin, grille 4px, sortir le disclaimer du header vers un « i ».
- **Dark/light** via tokens (signal « pro »). **Sources visibles** liées + `last_updated_*` (ADN
  crédibilité FT/Datawrapper). Tout open-source/self-host (Inter, IBM Plex, Protomaps CC-BY) = 0 € licence.
- **Refs** : Datawrapper (palettes choroplèthe), colorbrewer2.org, FT Visual Vocabulary, Protomaps
  basemaps, handsondataviz.org/design-choropleth.html.

---

## Ordre d'exécution suggéré
1. **Sprint « bugs/smooth »** (Partie A) : A1+A2 (tuiles, 1 build+deploy), A3 (slider). → re-build tuiles
   + redeploy + vérif chrome-devtools (couverture, pas de gaps, scrub fluide). Test pytest couverture.
2. **Sprint « recherche + landing »** : A4 (connecteur CP + index + MiniSearch + autocomplete), A5 (intro).
3. **Sprint « design »** (B3, surtout client-side, pas de rebuild tuiles) : tokens couleur + Inter/Plex
   + basemap vectoriel + légende/fiche refondues + dark/light.
4. **Sprint « data-viz »** (B1) : 3D fill-extrusion + animation play (quick) → bivarié E×T + Observable
   Plot (nécessitent `t_AAAAMM`/`e_bin`/`s_AAAAMM` dans `tiles.py`) → deck.gl (ambitieux, plus tard).
5. **Perf/caching** (B2) : à intégrer au fil (no-compress pmtiles + immutable hash = quick & important).

## Garde-fous (inchangés)
EPSG:2154 ; sources polies/cachées/tracées (`last_updated_*`) ; indice **indicatif** (jamais un
diagnostic) ; DVF agrégats communaux + `noindex` ; petits **commits atomiques** ; anti-OOM + API
tolérante au schéma ; `make lint` avant push ; rien de destructif / push / op VM sans accord.
