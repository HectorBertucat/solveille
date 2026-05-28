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
- **Page** : `meteo.data.gouv.fr` — jeu « Données mensuelles d'indice d'humidité des sols pour le dispositif catastrophes naturelles » (aussi sur data.gouv).
- **Format** : CSV — colonnes `numéro de maille, x (L93), y (L93), date, SWI` ; **pas mensuel** ⇒ valeurs **mensuelles** ; historique depuis **1960** (fichiers groupés par décennies). Licence Ouverte 2.0.
- **Usage** : par maille 8 km, calculer une **anomalie standardisée** vs la climatologie mensuelle de la maille ; rattacher chaque commune à sa/ses maille(s) (intersection ou centroïde).
- **⚠️ Important** : configuration SIM **spécifique au dispositif CatNat** → à n'utiliser **que** pour cet usage (c'est exactement le nôtre). Ne pas l'employer pour de l'humidité de surface générique.

## 5. Hub'eau Piézométrie (BRGM/ADES) — IPS nappes `T`
- **Base** : `https://hubeau.eaufrance.fr/api/v1/niveaux_nappes/`
  - `stations` — référentiel (filtres `bbox`, `code_commune`, `code_departement`, `code_bss`…) ; champs dont `code_commune_insee`, coordonnées.
  - `chroniques` — historique des niveaux (pour la **climatologie**), par `code_bss`.
  - `chroniques_tr` — **quasi temps réel horaire** (~1700 piézomètres télétransmis).
- **Jointure** : par `code_bss`. Mises à jour ADES intégrées **quotidiennement**.
- **Indicateur** : calculer un **IPS** (Indicateur Piézométrique Standardisé, méthode BRGM) — niveau courant rapporté à la distribution mensuelle historique (idéalement ≥ 30 ans, acceptable 15 ans). Cohérent par construction avec le SPI/SSWI. Stocker, par station, les **stats mensuelles** + la valeur courante ; recalcul incrémental.
- **Pièges** : couverture **inégale** → l'IPS est un **raffinement local** ; le SWI (grille 8 km, couverture totale) reste le signal dynamique **universel**. Paginer poliment (`size`, `page`), cacher, borner par département.

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
