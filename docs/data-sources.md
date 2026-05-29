# Playbook d'ingestion par source

Règles communes : reprojeter en **EPSG:2154** dès l'ingestion ; écrire le **brut horodaté** dans `data/raw/<source>/` + un `_meta.json` (date, version, hash, nb lignes) ; connecteurs **idempotents** et **polis** (cache, backoff, bornage). Les liens de **ressource** sur data.gouv changent : résoudre le dernier fichier via la page du dataset (ou l'API data.gouv) plutôt que de coder une URL en dur quand c'est évitable.

---

## 1. RGA 2026 — exposition argile (Géorisques / BRGM) — `E`
- **Quoi** : zonage d'aléa retrait-gonflement des argiles, version applicable aux ventes depuis le 1ᵉʳ juillet 2026. Classes : **faible / moyen / fort**.
- **Page** : `georisques.gouv.fr/donnees/bases-de-donnees/retrait-gonflement-des-argiles-version-2026` (téléchargement par département, **Shapefile, Lambert 93 / RGF93**). PMTiles communautaires aussi sur data.gouv (« Carte des risques retrait-gonflement des argiles »).
- **Usage** : intersecter avec les communes (ADMIN EXPRESS) → `part_alea_moyen`, `part_alea_fort`, `classe_dominante` par commune. Simplifier les polygones pour la carte.
- **Pièges** : gros volume → simplifier (mapshaper/`ST_Simplify`) ; EPSG:2154 ; hors Paris pour certaines couches.

## 2. Communes basculant RGA 2026 — flag reclassement
- **Page** : data.gouv `communes-basculant-en-classe-dexposition-rga-au-1er-juillet-2026`.
- **Format** : CSV (UTF-8, virgule), colonnes `code_insee, slug, nom_commune, code_departement, nom_departement, rga_classe_2020, rga_classe_2026, bascule_type`.
- **Usage** : alimenter `commune.basculement_2026` (et la mise en avant UI « commune reclassée »).

## 3. Exposition maisons / RGA par EPCI (Fideli) — stock `J`
- **Page** : data.gouv `exposition-des-maisons-individuelles-au-phenomene-de-retrait-gonflement-des-argiles-rga`.
- **Quoi** : nb de maisons individuelles exposées **par EPCI**, ventilé par **niveau RGA** (1 faible / 2 moyen / 3 fort) et **période de construction** (base Fideli 2021).
- **Usage** : `epci_stock` ; rattacher l'EPCI à ses communes pour estimer un stock communal (clé de répartition simple, à documenter).
- **Pièges** : maille **EPCI** (pas commune) → la descente à la commune est une approximation ; la période de construction sert à pondérer la vulnérabilité.

## 4. SWI CatNat (Météo-France) — anomalie humidité des sols `T`
- **Jeu** : data.gouv `donnees-mensuelles-dindice-dhumidite-des-sols-pour-le-dispositif-**catnat**`, id interne `69380f267975cac439339b63`. ⚠️ **Pas** l'homonyme `…-catastrophes-naturelles` (mono-ressource = lien 302 vers un portail JS Météo-France **inutilisable** au fetch). Résoudre les ressources via l'API dataset (`/api/1/datasets/<id>/`) puis télécharger chaque `latest` (`/api/1/datasets/r/<uuid>`, 302 → CDN `static.data.gouv.fr`). Licence Ouverte 2.0, Météo-France.
- **Ressources** : **7 fichiers `.csv.gz` décennaux** (`swi.196001-196912` … `swi.202001-2025xx`, ~5 Mo chacun) + **1 fichier grille** (`caracteristiques-geographiques-mailles-swi.csv`, ~450 Ko) + un PDF. Seul le fichier de la décennie courante évolue (mensuel) ; les décennies closes sont immuables.
- **Format SWI** (en-tête réel, **guillemets**) : `"NUMERO";"LAMBX";"LAMBY";"DATE";"SWI_UNIF_MENS"`. Délimiteur `;`, décimal `.`, UTF-8. `DATE` = **`AAAAMM`** (ex. `202504`). **`LAMBX`/`LAMBY` sont déjà en Lambert 93 (EPSG:2154), en mètres** (≡ `lambx93`/`lamby93` de la grille) → **pas de reprojection**. `SWI_UNIF_MENS` = valeur mensuelle = **moyenne glissante 3 mois** des SWI quotidiens. Échelle ~0–1 (0 sec, 1 saturé) mais **déborde un peu** (observé `[-0.04 ; 1.45]`) → **ne pas clamper**, c'est une valeur brute, l'anomalie est à calculer.
- **Grille** : 5 lignes de commentaire `#` (dont la ligne d'en-tête `#num_maille;lambx;lamby;lambx93;lamby93;lat_dg;lon_dg`). `lambx`/`lamby` = Lambert II étendu en **hectomètres** (ignorer) ; **`lambx93`/`lamby93` = centroïde maille en mètres L93** (à utiliser). Lire avec `comment='#'`, `header=false`, colonnes positionnelles.
- **Maille** : **8 981 mailles** de **8 km** de côté, fixes. Le jeu ne fournit que des **centroïdes** → reconstruire le carré 8 km (`x93±4000`, `y93±4000`) pour l'intersection surfacique avec la commune.
- **Couverture / cadence** : mensuel, **depuis 1960-01** (`196001`) jusqu'au mois courant -1/-2 (dernier dispo ~2025-12). Job **mensuel** (re-télécharger seulement la décennie courante). Tracer `last_updated_swi` = `last_modified` de la ressource.
- **Volumétrie** : ~8981 × 12 × ~66 ans ≈ **7 M lignes** au total (quelques centaines de Mo CSV brut) → trivial pour DuckDB (lit le `.csv.gz` directement).
- **Usage** : climatologie **par maille et par mois calendaire** (tout l'historique) → anomalie standardisée `z_SWI = (swi − μ_mois)/σ_mois` ; rattacher maille↔commune par aire d'intersection.
- **⚠️ Important** : configuration SIM **« uniforme » spécifique au dispositif CatNat** → à n'utiliser **que** pour cet usage (c'est exactement le nôtre). Ne pas l'employer pour de l'humidité de surface générique.

## 5. Hub'eau Piézométrie (BRGM/ADES) — IPS nappes `T`
*Vérifié live (API v1.4.3, mai 2026). Licence Ouverte 2.0 — ADES/BRGM (OFB). Doc : `hubeau.eaufrance.fr/page/api-piezometrie`, OpenAPI `…/niveaux_nappes/api-docs`.*
- **Base** : `https://hubeau.eaufrance.fr/api/v1/niveaux_nappes/` (formats `json`/`geojson`/`csv`).
  - `stations` — référentiel **national ~23 308 stations** (dont ~5800 actives/exploitables). Filtres **fonctionnels** : `code_departement`, `code_commune`, `bbox`, `code_bss`, `nb_mesures_min`. Champs : `code_bss`, `code_commune_insee`, `x`/`y` (**WGS84, lon=x/lat=y** → reproj 2154 `always_xy`), `date_debut/fin_mesure`, `nb_mesures_piezo`, `codes_bdlisa`, `profondeur_investigation`.
  - `chroniques` — historique des niveaux (pour la **climatologie**). Champ niveau = **`niveau_nappe_eau`** (cote **NGF**, m) ; aussi `profondeur_nappe` (m sous le sol, **signe inverse**). `date_debut/fin_mesure` fonctionnels.
  - `chroniques_tr` — **temps réel horaire** (**~1 400 piézomètres** télétransmis), champ **`niveau_eau_ngf`** (même référentiel NGF), **~3 mois d'historique seulement**.
- **⚠️ Piège majeur** : `chroniques` et `chroniques_tr` **IGNORENT silencieusement tout filtre géographique** (`code_departement`/`code_commune` renvoient les ~25 M lignes nationales, **sans erreur 400**) → lister d'abord les `code_bss` via `stations?code_departement=`, puis **boucler par `code_bss`** (jamais de `chroniques` sans `code_bss`). Pagination plafond **dur `page×size ≤ 20000`**, URL ≤ 2083 car, **pas de curseur** → fenêtrer par dates au-delà.
- **Signe** : NGF élevé = nappe haute = **humide** (anomalie négative = sec). Standardiser le **NGF** (`niveau_nappe_eau`) ; `profondeur_nappe` aurait le signe inverse.
- **Indicateur** : **IPS** (Indicateur Piézométrique Standardisé) **non servi par l'API → recalcul** (méthode BRGM Seguin 2014/RP-67249 : climatologie mensuelle par `code_bss` → **NQT** → quantile normal ; 7 classes ; ≥ 15 ans, idéal ≥ 30). Voir `metric.md` et ADR-018. Stocker stats mensuelles + valeur courante ; recalcul incrémental.
- **Confiance** : couverture **très inégale** → l'IPS est un **raffinement local** (le SWI 8 km reste universel). Pondérer par historique + **libre vs captive** (via `codes_bdlisa` → référentiel BDLISA externe, M2). Paginer poliment (`size`≤20000, séquentiel par `code_bss`), cacher, borner par département.

## 6. GASPAR — Cat-Nat sécheresse (DGPR) — calibration `H`
- **Page** : data.gouv `base-nationale-de-gestion-assistee-des-procedures-administratives-relatives-aux-risques-gaspar` (et couche Géorisques « procédures administratives »). CCR publie aussi la liste des arrêtés (J+1/J+2).
- **Usage** : filtrer les arrêtés **sécheresse / RGA**, clé **code INSEE**, dates → `catnat_secheresse` (fréquence, dernier arrêté). En v2, relier états SWI/IPS passés ↔ années de reconnaissance pour un seuil empirique.
- **Pièges** : aléas multiples dans GASPAR (filtrer sécheresse géotechnique) ; reconnaissance dépend aussi de critères **administratifs** → calibration **indicative**. Maj < 30 j après J.O.

## 7. DVF géolocalisé (DGFiP) — prix médian maison `J`
- **Page** : data.gouv `demandes-de-valeurs-foncieres-geolocalisees` (CSV/an, ~5 dernières années ; lat/lon **WGS84**, `valeur_fonciere`, `type_local`, `surface_reelle_bati`, `code_commune`…). API DVF+ alternative.
- **Usage** : agréger par commune → **prix médian maison** (€/m²), nb de transactions de maisons 12 mois, et nb en zone exposée (intersection RGA). Reprojeter en 2154 pour les jointures.
- **⚠️ Légal** : **agrégats communaux uniquement** ; **pas** d'exposition de transactions nominatives ; **pas d'indexation moteurs** (`noindex`) ; pas de réidentification (R112 A-3 LPF).

## 8. ADMIN EXPRESS (IGN) — géométries communes
- **Quoi** : contours communaux officiels (EPSG:2154). Base de toutes les jointures spatiales et de la carte.
- **Usage** : `commune.geom_2154` ; simplifier pour la carte. Aligner sur le **COG** courant (gérer les fusions de communes).

## 9. INSEE population (option, v2) — pondération
- **Quoi** : population communale / carroyée 200 m, structure d'âge. Pour pondérer l'enjeu par population exposée (raffinement).

---

### Cadences d'ingestion (scheduler)
| Source | Fréquence du job |
|---|---|
| Hub'eau `chroniques_tr` | quotidien |
| SWI CatNat | mensuel |
| GASPAR | hebdomadaire (ou à l'événement) |
| DVF | semestriel (avril / octobre) |
| RGA 2026 / Communes basculées | à l'événement (maj zonage) |
| ADMIN EXPRESS / Fideli / INSEE | annuel |
