# Roadmap

Construire dans l'ordre. Chaque incrément doit être démontrable et utile en soi.

## v0 — « La carte de l'enjeu » (ship rapide, sans dynamique)
**But** : carte nationale + fiche commune montrant l'exposition argile et l'enjeu, sans la météo.
- [x] `make setup` : venv (uv), deps, extensions DuckDB (`spatial`,`httpfs`).
- [x] Ingestion **ADMIN EXPRESS** → `commune(geom_2154)` (GPKG v4, 34 746 communes).
- [x] Ingestion **RGA 2026** → intersection → `commune_rga(part_alea_*, classe_dominante)` (FeatureServer, repli validé).
- [x] Ingestion **communes basculées 2026** → `commune.basculement_2026` (133 communes).
- [x] Ingestion **Fideli EPCI** → `epci_stock` → descente commune (clé stock INSEE × exposition).
- [x] Ingestion **DVF** → `commune_dvf(prix_median_maison, n_tx_*)` (agrégats only).
- [x] Calcul `E` et `J` → `commune_pression` (sans `T`). National : ~1954 Md€ de bâti exposé.
- [x] **PMTiles** (tippecanoe) + **MapLibre** : choroplèthe `E` + mise en avant des communes reclassées.
- [x] **FastAPI** : `/communes/{insee}`, `/meta` (+ service statique front/tuiles).
- [x] Tests : schéma connecteurs, contrats de données (volumétrie/nulls/SRS), `E∈[0,1]` (43 tests).
- [x] Bandeau de cadrage + `noindex`.
**Démonstration v0** : « où se concentre la valeur de bâti exposé, et quelles communes changent de classe pour les ventes 2026 ». ✅ **Livré.**

## v1 — « La boussole » (nowcast dynamique)
**But** : ajouter la tension hydrique du moment. Phasé : **v1.0 = SWI** (signal universel, 100 % des communes) → **v1.1 = IPS Hub'eau** (raffinement local). Voir ADR-015/016/017.

### v1.0 — SWI (la dynamique nationale)
- [ ] Ingestion **SWI CatNat** (CDN data.gouv `…-catnat`) → `swi_maille`, `swi_grille`.
- [ ] Climatologie mensuelle/maille (tout l'historique) → anomalie standardisée `z_SWI` (`swi_anomalie`).
- [ ] Rattachement maille SWI ↔ commune (carré 8 km ∩ commune, pondéré par aire) → `commune_swi`.
- [ ] Calcul `T = dry_SWI` puis `IP-RGA = round(100·E·T^0.8)` (5 niveaux, quantiles nationaux) → `commune_pression_mensuel` + statique `*_latest`.
- [ ] **Curseur de date** dans l'UI (niveau IP-RGA par mois en attribut de tuile `n_AAAAMM`) + sparkline de pression dans la fiche.
- [ ] **systemd timer** mensuel SWI ; `last_updated_swi` exposé.
- [ ] Tests métier : monotonie (plus sec ⇒ score ≥), `E=0 ⇒ 0`, **couverture nationale 100 % via SWI**, cohérence temporelle (mois sec connu > mois humide).

### v1.1 — IPS Hub'eau (raffinement local)
- [ ] Ingestion **Hub'eau** (`stations` par dept → `code_bss` ; `chroniques` pour la climatologie ; `chroniques_tr` quotidien) → `piezo_ips` (recalcul IPS, classes BRGM).
- [ ] Rattachement piézo ↔ commune (+ **niveau de confiance**) ; pondération `w_ips` dans `T`.
- [ ] **systemd timer** quotidien nappes.
**Démonstration v1** : carte de pression qui évolue dans le temps, par commune.

## v2 — « Calibration + agent »
**But** : profondeur analytique et interface agent.
- [ ] **GASPAR** sécheresse → `catnat_secheresse` ; relation empirique SWI/IPS ↔ années de reconnaissance → lecture « X % des situations ayant mené à un arrêté » (`H`, indicatif).
- [ ] Analyse de tendance (rapprochement du seuil).
- [ ] **Serveur MCP** : interroger la pression d'une commune/adresse en langage naturel (réutilise `/lookup`).
- [ ] (Option) raffinement **IRIS** ; pondération **population INSEE**.
**Démonstration v2** : « les conditions actuelles ressemblent à X % des situations passées à risque ici » + requêtes en langage naturel.

## Transverse (tout du long)
- Lint **ruff** + typage **mypy** + **pytest** ; CI légère (lint+tests sur échantillon).
- Manifestes `_meta.json` par source ; logs structurés ; doc à jour.
- Commits atomiques (Conventional Commits) ; rien de destructif sans accord.
