# Journal des décisions (ADR)

Format léger : Décision · Contexte · Conséquences.

## ADR-001 — Maille = commune
**Décision** : la commune est la maille primaire ; l'IRIS reste un raffinement optionnel (v2+).
**Contexte** : DVF, GASPAR, le zonage RGA et les arrêtés Cat-Nat se rattachent naturellement au code INSEE commune ; bon compromis lisibilité / volumétrie / couverture nationale ; l'IRIS exigerait des données plus lourdes et moins universelles.
**Conséquences** : Fideli (maille EPCI) doit être redescendu à la commune via une clé de répartition documentée (approximation assumée).

## ADR-002 — Périmètre national
**Décision** : modèle de données et carte **nationaux** dès le départ.
**Contexte** : volumétrie modeste (SWI mensuel léger, DVF→Parquet, polygones simplifiés) → le national tient sur la VM ; un projet vitrine gagne en portée.
**Conséquences** : soigner la simplification des géométries et la pagination des API pour rester dans l'enveloppe ressources.

## ADR-003 — Cadence « nappes quotidiennes + SWI mensuel », mode poli
**Décision** : pas de temps réel à la seconde ; dynamique = SWI **mensuel** + IPS nappes **quotidien** ; ingestions **idempotentes**, **cachées** et **bornées**.
**Contexte** : l'utilisateur ne veut pas surcharger le serveur ni les API publiques ; le phénomène RGA évolue lentement.
**Conséquences** : afficher `last_updated_*` ; la « fraîcheur » est honnête (mensuelle/quotidienne), pas instantanée.

## ADR-004 — DuckDB sans serveur + Parquet
**Décision** : DuckDB (`spatial`,`httpfs`) sur fichiers Parquet, pas de SGBD serveur.
**Contexte** : 8 Go de RAM ; jointures spatiales et fenêtres percentiles bien supportées par DuckDB ; déjà maîtrisé.
**Conséquences** : privilégier les requêtes sur Parquet (streaming) plutôt que le chargement mémoire complet.

## ADR-005 — PMTiles statiques pour la carte
**Décision** : choroplèthe servie via PMTiles (tippecanoe), fichier statique.
**Contexte** : cheap, rapide, range requests, 20 To d'egress largement suffisants ; évite un tile-server.
**Conséquences** : régénérer les tuiles quand `commune_pression` change.

## ADR-006 — DVF en agrégats uniquement
**Décision** : n'exposer que des agrégats communaux ; pages `noindex` ; pas de réidentification.
**Contexte** : contrainte légale DVF (R112 A-3 LPF : pas d'indexation moteurs, pas de réidentification).
**Conséquences** : pas de listing de transactions ; le prix médian commune suffit pour l'enjeu.

## ADR-007 — Indice indicatif, pas un diagnostic
**Décision** : positionner Solveille comme **indice territorial indicatif**.
**Contexte** : le RGA à l'échelle d'un bâtiment dépend de facteurs locaux non modélisés ; éviter tout faux signal de sécurité structurelle ou conseil.
**Conséquences** : cadrage explicite dans l'UI ; pas de score par maison ; calibration `H` présentée comme indicative.

## ADR-008 — AGENTS.md source de vérité, CLAUDE.md pointeur
**Décision** : règles d'agent dans `AGENTS.md` (tool-agnostic) ; `CLAUDE.md` importe via `@AGENTS.md` + surcharges Claude Code.
**Contexte** : portabilité entre assistants (Codex/Cursor/Copilot lisent AGENTS.md) ; Claude Code lit nativement CLAUDE.md.
**Conséquences** : éviter la divergence des deux fichiers ; technicité fine déléguée aux skills (chargés à la demande).

## ADR-009 — Subagents read-only
**Décision** : les subagents (`.claude/agents/`) n'éditent pas ; ils renvoient des constats ; les écritures restent au parent.
**Contexte** : bonne pratique Claude Code (les outils d'édition d'un subagent contournent les prompts d'approbation).
**Conséquences** : recherche/relecture/validation déléguées ; application centralisée et contrôlée.

## ADR-010 — Nom de travail « Solveille »
**Décision** : nom **Solveille** (mot-valise *sol* + *veille*).
**Contexte** : « RGA » est opaque ; il fallait un nom évocateur (surveillance des sols), libre (Argiléo déjà pris par un enduit). Alternatives : Terravigie, Glaise, Boussole.
**Conséquences** : renommable en un remplacement global (`solveille` → autre) si besoin.

## ADR-011 — ADMIN EXPRESS COG CARTO via Géoplateforme (GeoPackage v4)
**Décision** : géométries communes = **ADMIN EXPRESS COG CARTO**, livraison **GeoPackage v4** `LAMB93_FXX` (métropole + Corse, déjà EPSG:2154), téléchargée via le **flux Atom data.geopf.fr** (résolution dynamique de la dernière édition — pas d'URL en dur).
**Contexte** : geoservices.ign.fr redirige vers cartes.gouv.fr ; le vrai moteur de téléchargement est `data.geopf.fr`. Le SHP historique (attributs MAJUSCULES Latin-1) est remplacé depuis 2025 par le GPKG v4 (attributs minuscules BD TOPO : `code_insee`, `nom_officiel`, `codes_siren_des_epci`…). Édition courante résolue : 2026-01-01. Couche `commune` (34 746 communes), géométrie `geometrie`. Archive `.7z` → dépendance `py7zr`.
**Conséquences** : COG CARTO (continu, aligné COG) plutôt que « vérité terrain » ; DROM hors v0 (RGA métropole only) ; `codes_siren_des_epci` fournit le rattachement commune→EPCI.

## ADR-012 — Acquisition RGA 2026 : FeatureServer ArcGIS (repli) + simplification 25 m
**Décision** : ingérer le zonage RGA 2026 via le **FeatureServer ArcGIS hébergé** (copie Esri France / MRN), bornable par département, sortie GeoJSON ; reprojection 4326→2154 (`always_xy`), **simplification topologique 25 m**.
**Contexte** : le SHP officiel Géorisques (Licence Ouverte 2.0) n'est **pas automatisable** (formulaire JS, absent de la Géoplateforme et de data.gouv en vecteur ; data.gouv n'expose qu'un PMTiles WebMercator inutilisable pour les intersections). Le FeatureServer expose la couche **dissoute par (département × niveau)** (attributs `DPT`/`NIVEAU` 1·2·3/`ALEA`), aires réalistes. 1,34 M de sommets bruts → OOM ; la simplification 25 m (~5× moins de sommets, ~1 % d'écart d'aire) tient dans 8 Go.
**Conséquences** : **double attribution** (Géorisques/BRGM Etalab 2.0 *source* + Esri France/MRN ODbL *diffusion*), tracée dans le manifeste ; à revisiter si une diffusion vecteur officielle apparaît. Couverture France métropole **hors Paris (75)** : absence ≠ aléa nul (`has_rga_coverage`).

## ADR-013 — INSEE logement ajouté en v0 ; clé de descente Fideli stock × exposition
**Décision** : pour redescendre le stock Fideli (maille EPCI) à la commune, ajouter en **v0** la source INSEE **base chiffres-clés Logement 2022** (`base-cc-logement-2022`, `CODGEO`/`P22_MAISON`). Clé de répartition documentée : `w_c = P22_MAISON_c × part_alea_moyen_fort_c`, puis `maisons_exposées_c = maisons_exposées_EPCI × w_c / Σ_EPCI(w)`.
**Contexte** : `docs/data-sources.md §9` ne prévoyait l'INSEE qu'en v2 (population). Le simple prorata de surface exposée ignore la densité de bâti ; combiner stock (INSEE) et localisation de l'argile (RGA∩commune) est plus juste et se réduit au prorata du stock si l'exposition est uniforme.
**Conséquences** : +1 connecteur v0 (`insee_logement`) ; harmonisation COG nécessaire (cf. ADR-014) ; caveat : les comptages Fideli reposent sur le zonage **BRGM 2020**, pas le RGA 2026 — à afficher.

## ADR-014 — Périmètre des runs (Occitanie → national) & harmonisation COG
**Décision** : connecteurs **bornables par département** dès le départ ; validation du pipeline sur l'**Occitanie** (`31,09,11,32,81,82`) puis run **national**. Modèle/schéma nationaux dans tous les cas (ADR-002).
**Contexte** : itération QA rapide et polie sans matraquer les API ni saturer la VM ; trois millésimes COG coexistent (**ADMIN EXPRESS 2026 / INSEE logement 2025 / Fideli EPCI 2021**).
**Conséquences** : prévoir des **anti-jointures** pour lister les codes orphelins (commune↔EPCI, commune↔INSEE) et une table de passage si le taux est matériel ; bornage via `SOLVEILLE_DEPARTEMENTS`.

## ADR-015 — Acquisition SWI CatNat via CDN data.gouv + grille pour la géométrie maille
**Décision** : ingérer le SWI CatNat depuis le jeu data.gouv **`…-catnat`** (id `69380f267975cac439339b63`) — 7 CSV.gz décennaux + 1 fichier grille — résolus par l'**API dataset** puis téléchargés via les `latest` (`/api/1/datasets/r/<uuid>`, 302 → CDN `static.data.gouv.fr`). Climatologie de l'anomalie `z_SWI` sur **tout l'historique (1960→)**.
**Contexte** : l'homonyme `…-catastrophes-naturelles` est mono-ressource = lien 302 vers un **portail JS Météo-France** (id_produit=301) non automatisable (même piège que le SHP RGA, ADR-012). Le bon jeu, lui, sert des fichiers **directs et stables**. Les `LAMBX`/`LAMBY` des fichiers SWI sont **déjà en L93** (mètres) ≡ `lambx93`/`lamby93` de la grille → pas de reprojection. Le jeu ne fournit que des **centroïdes** → on reconstruit le carré 8 km pour l'intersection communale. SWI = moyenne glissante **3 mois**, échelle ~0–1 qui **déborde** (`[-0.04;1.45]` observé) → non clampée.
**Conséquences** : connecteur `GET` poli/idempotent (~8 requêtes/mois, cache conditionnel) ; UUIDs en constantes de repli si l'API change ; `last_updated_swi` = `last_modified` ressource. Climatologie longue assumée (caveat tendance climatique, `docs/metric.md`).

## ADR-016 — Mart temporel : split statique/mensuel + niveau-par-mois en attribut de tuile
**Décision** : v1 rend la pression **temporelle**. Deux marts : `commune_pression` (statique, 1 ligne/commune, colonnes v0 + dernier mois `*_latest`) et **`commune_pression_mensuel`** (mince, 1 ligne/(insee, mois) sur la **fenêtre 2017→**). La carte porte le **niveau IP-RGA (0–5)** de chaque mois comme **attribut de tuile** `n_AAAAMM` ; le curseur de date pilote `fill-color` par `["get", "n_"+mois]`.
**Contexte** : dupliquer les colonnes statiques × ~108 mois est inutile ; le split garde l'API/les tests v0 quasi intacts et le mensuel léger. PMTiles **statique unique** (pas de tile-server, ADR-005) avec un attribut octet par mois reste raisonnable (~108 entiers 0–5 / commune) ; le score précis et la série vivent dans l'API (`/communes/{insee}/serie`).
**Conséquences** : régénérer les tuiles quand le mensuel change ; fenêtre servie bornée (2017→) distincte de la climatologie (tout l'historique) ; `z_ips`/`dry_ips` présents mais NULL en v1.0 (brancher l'IPS en v1.1 sans migration).

## ADR-017 — IPS Hub'eau recalculé, reporté en v1.1
**Décision** : v1.0 = **SWI seul** (`T = dry_SWI`, couverture 100 % des communes). L'IPS piézométrique (Hub'eau) arrive en **v1.1** comme **raffinement local** pondéré (`w_ips`) avec niveau de confiance.
**Contexte** : aucun endpoint Hub'eau ne sert l'IPS (recalcul KDE+CDF→quantile normal, grille BRGM 7 classes, ≥15 ans d'historique) ; `chroniques` n'accepte **aucun filtre géographique** (lister les `code_bss` via `stations?code_departement=`, plafond dur `page×size ≤ 20000`) ; couverture très inégale (référentiel **~23 308 stations**, ~5800 actives/exploitables, ~temps réel **~1 400** ; chiffres corrigés en v1.1, cf. §5/ADR-018) → c'est bien un raffinement, pas le signal universel. Le SWI porte seul une boussole nationale crédible et vérifiable.
**Conséquences** : première livraison v1 plus rapide et contrôlable ; le schéma mensuel réserve déjà `z_ips`/`dry_ips` ; piège NGF vs profondeur (signe) et noms de champs `niveau_nappe_eau`↔`niveau_eau_ngf` à gérer en v1.1.

## ADR-018 — IPS v1.1 : NQT (classes BRGM) **+** z plain (pilote T), rattachement spatial, `w_ips = confiance·0,5`
**Décision** : l'IPS piézométrique recalculé stocke **deux standardisations** du niveau NGF de la station, contre la climatologie du **même mois calendaire** (tout l'historique, ≥ 15 ans) :
- **`z_ips` plain** `(x−μ)/σ` → `dry_IPS = sigma(−GAIN·z_ips)` → **pilote la tension `T`** (même méthode que `z_SWI` ⇒ cohérence inter-signaux) ;
- **`ips_nqt = Φ⁻¹(position de Weibull `r/(n+1)`)`** (NQT, méthode BRGM, **N(0,1) par construction**) → **pilote la classe BRGM** affichée (7 classes aux seuils standard-normaux `[−1.282,−0.842,−0.253,+0.253,+0.842,+1.282]` = percentiles 10/20/40/60/80/90 %) et le **test de cohérence**.
Combinaison : `T = (w_swi·dry_SWI + w_ips·dry_IPS)/(w_swi+w_ips)`, `w_swi=1`, **`w_ips = confiance · W_IPS_MAX`** (`W_IPS_MAX=0,5` → SWI dominant). `confiance ∈ [0,1] = clamp01(f_hist·f_nappe·f_repr)` (historique : 0 si <15 ans, plancher 0,4 à 15 ans → 1,0 à 30 ans ; `f_nappe`/`f_repr`=1 en M1). **`confiance=0` ⇒ `w_ips=0` ⇒ `T=dry_SWI`** (repli SWI universel). Rattachement station↔commune par **point-dans-commune** (`ST_Contains`, coords reprojetées 2154), repli `code_commune_insee`, agrégat communal pondéré par confiance.
**Contexte** : « centré-réduit » et « classes BRGM » sont **subtilement incompatibles** sur des niveaux de nappe non-gaussiens (bornés, asymétriques) — un z plain de −1,282 n'est le 10ᵉ percentile que si la loi est normale. La **NQT** réconcilie les deux (sortie N(0,1) ⇒ classes exactes) ; on la retient pour la classe officielle, tout en pilotant `T` par le z plain pour rester homogène avec `z_SWI`. Les deux sont **monotones croissants** dans le NGF ⇒ même sens sec/humide (calibration seule diffère). `Φ⁻¹` via **macro DuckDB `probit`** (approximation d'Acklam, ~3e-9 vs `metric.probit`=`NormalDist`, parité testée à 1e-6) — les UDF Python DuckDB exigent numpy (hors stack). Climatologie sur **tout l'historique de la station** (cohérent ADR-015 SWI ; peu de stations couvrent 1981-2010), période **paramétrable**.
**Conséquences** : M1 livre l'IPS par `code_bss` + rattachement commune + test de cohérence (`commune_ips` produit mais **pas encore joint au mart** : `T`/`dry_ips`/`confiance_t` branchés en **M2**). Caveat de **stationnarité** (la NQT BRGM la suppose ; nappes à tendance pluriannuelle) et d'**asymétrie méthodologique** (z plain pour `T`, NQT pour la classe) à afficher. L'**agrégation communale de la NQT** (moyenne pondérée) n'est plus strictement N(0,1) — biais faible, communes multi-stations rares.

## ADR-019 — v2 `H` : source GASPAR bulk + calibration empirique (SWI seul, CDF des sévérités-pics, pooling départemental)
**Décision** — **Source** : ingérer GASPAR via l'**archive nationale `gaspar.zip`** (résolue par l'API data.gouv, Licence Ouverte 2.0), dont on n'extrait que **`catnat_gaspar.csv`** ; filtre sécheresse sur `lib_risque_jo='Sécheresse'` (≡ code `num_risque_jo='SEC'`, **texte**), **dédup (commune × `cod_nat_catnat`)**, agrégat `catnat_secheresse` par commune (`catnat_freq`, `premier/dernier_arrete`, `annees_reco[]`, `evenements[]{dat_deb,dat_fin,annee}`). **Indice `H`** (lecture complémentaire, **hors `ip_rga_score`**) = **CDF empirique** : sévérité `s=−z_SWI` ; **sévérité-pic** `s_evt=max(−z_SWI)` par évènement reconnu sur sa fenêtre **bornée à `H_EVENT_MAX_MONTHS=24` mois** ; **pool départemental** des `{s_evt}` (repli **national** si `< H_MIN_POOL_DEPT=30`) ; `h_proba=#{s_evt≤s_now}/#pool` du mois servi. Substrat `z_SWI` historique (**`SWI_CALIB_FROM=1990`→**) via réutilisation **paramétrée** de `build_swi_anomalie`/`build_commune_swi` (pas de SQL neuf lourd). **SWI seul** ; IPS reporté.
**Contexte** : (a) l'**API REST** `gaspar/catnat` n'a **pas de filtre département** (500) → ~35k appels per-INSEE pour le national ; le bulk (`gaspar.zip` ~6,3 Mo → CSV 260 799 lignes, **47 576** sécheresse, 1990→2025) est poli et tractable. `num_risque_jo` est un **code mnémonique texte** (`SEC`), **pas l'entier** « 18 » de catnat.net (vérifié live) → filtre libellé + self-check. (b) Le **SWI CatNat est l'indice officiel** d'instruction sécheresse ⇒ forte cohérence ; `z_SWI` déjà **standardisé par maille×mois** ⇒ seuil de reconnaissance assez homogène, le pool **départemental** capte le résidu. (c) Les périodes GASPAR sont **très hétérogènes** (0 à ~160 mois, médiane 5) → le **cap 24 mois** évite qu'un `max` capte un outlier sans rapport. (d) GASPAR = **reconnaissances seulement** (positifs) → `H` est un **percentile de calibration**, *pas* une probabilité de reconnaissance. (e) Le national tourne en **~3 s** (substrat 15 M lignes + `H` 3,75 M) — pas d'OOM.
**Conséquences** : M-A livre `catnat_secheresse` (orphelins COG ~0,2 %, Toulouse `freq=19`) ; M-B livre `commune_h` (validé `metric-validator`). **Caveat assumé & affiché** : asymétrie **pic-de-fenêtre vs mois courant** ⇒ `H` **conservateur** — il ne « monte » nettement que lors d'une sécheresse marquée (national : 2017-01 / 2022-05 ⇒ ~52 % des communes `H>0,5`), comportement voulu d'une boussole complémentaire. `H` **affiché si `E>0`** (gating au mart, M-C). Évènements **sans `z_SWI` mesurable** (avant 1990 / trous) exclus du pool ⇒ `H_MIN_POOL_DEPT` compte les évènements **mesurables**. Tout est **paramétrable** (`SWI_CALIB_FROM`/`H_EVENT_MAX_MONTHS`/`H_MIN_POOL_DEPT`, pooling) ; pistes futures : tier commune-propre (≥N arrêtés), IPS dans `H`.

## ADR-020 — v3 B-vec : fond de carte **vectoriel Protomaps self-hosté** + glyphs locaux (labels au-dessus du choroplèthe)
**Décision** : remplacer le **raster CARTO** (`light_all/dark_all`, labels noyés SOUS le fill) par un fond **vectoriel** servi 100 % depuis notre origine : un extrait **France `france.pmtiles`** du planet **Protomaps** (z0–12, bbox métro + Corse), servi par le mount StaticFiles `/tiles` en **requêtes Range** — exactement la plomberie de `communes.pmtiles`. Les couches du style sont **pré-générées** (`tools/gen_basemap.mjs` depuis `@protomaps/basemaps@5.7.2`, flavors clair/sombre, `lang:'fr'`) en un asset statique `front/basemap-layers.js` (`window.PM_BASEMAP`), **icônes retirées** (drop `roads_oneway`/`roads_shields`/`pois`, dé-iconisation de `places_locality`) ⇒ **aucun sprite à self-host**. Le choroplèthe (`communes-*`) est inséré **juste avant la 1ʳᵉ couche `symbol`** (calculée à l'exécution) ⇒ **les noms de lieux passent au-dessus du fill** ; `fill-opacity` 0.78→0.62. **Glyphs self-hostés** : les 3 fontes `Noto Sans Regular/Medium/Italic` (256 plages complètes, ~13 Mo) téléchargées par `make glyphs` depuis `protomaps/basemaps-assets` **épinglé** (SIL OFL), servies en `/glyphs/{fontstack}/{range}.pbf` ; `localIdeographFontFamily` pour les idéogrammes. Bascule clair/sombre = `map.setStyle(buildStyle(theme))` + ré-application de l'état (mois, 3D) sur `styledata`.
**Contexte** : seul un fond vectoriel met les labels au-dessus du fill (lecture pro type Datawrapper/FT). **CARTO-vectoriel écarté** : sa `LICENSE` interdit l'usage public gratuit du service de tuiles (or `argile.hectorb.fr` l'est), et c'eût été troquer une dép. distante qui marche contre une plus fragile (clé requise du jour au lendemain → carte blanche) tout en exigeant **quand même** des glyphs self-hostés. Protomaps coche tout : 0 dép. runtime, réutilise Range/StaticFiles, RAM ~0 (Range, pas de mmap), egress négligeable (tuiles vectorielles cachées). **Garde-fou #1 : un seul glyph 404 = carte NOIRE** → couverture **complète** des 3 fontes (la nav. européenne demande même les plages cyrillique/arabe, vérifié) + test offline `test_basemap_glyphs.py` (chaque `text-font` du style ⊂ `_FONTSTACKS`, 0 `icon-image`). `france.pmtiles` ne monte qu'à **z12** = zoom max interactif du front (au-delà = tuiles jamais demandées ; extrait z9 local = 82 Mo en 6 s, z12 VM ~1–1,5 Go).
**Conséquences** : front sans dép. CDN pour le fond (maplibre/pmtiles/minisearch restent en CDN). `front/glyphs/` **gitignoré** (régénéré par `make glyphs`, prérequis de `make tiles`) ; `france.pmtiles` **gitignoré** (bâti par `make basemap` / `deploy/build-basemap.sh` — go-pmtiles + extract du dernier planet daté `build.protomaps.com`, ~6 j de rétention → sonder une date récente, ne pas figer). Caddy : `*.pmtiles` **exclu de `encode`** (Range), `/glyphs/*` cache long, `basemap-layers.js` en `no-cache` (revalidation ETag). **Déploiement VM** (accord requis) : push (CI = code) PUIS `make glyphs` + `make basemap` + `systemctl reload caddy`. **Validé local** (chrome-devtools, contexte frais) : labels au-dessus national + ville, clair/sombre, 3D, recherche/fiche, 206 Range, 0 console error. Bascule perf/MVT (B-perf) inchangée et toujours à faire.
