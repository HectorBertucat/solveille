# Roadmap

Construire dans l'ordre. Chaque incrément doit être démontrable et utile en soi.

## v0 — « La carte de l'enjeu » (ship rapide, sans dynamique)
**But** : carte nationale + fiche commune montrant l'exposition argile et l'enjeu, sans la météo.
- [ ] `make setup` : venv (uv), deps, extensions DuckDB (`spatial`,`httpfs`).
- [ ] Ingestion **ADMIN EXPRESS** → `commune(geom_2154)`.
- [ ] Ingestion **RGA 2026** → intersection → `commune_rga(part_alea_*, classe_dominante)`.
- [ ] Ingestion **communes basculées 2026** → `commune.basculement_2026`.
- [ ] Ingestion **Fideli EPCI** → `epci_stock` → descente commune (clé documentée).
- [ ] Ingestion **DVF** → `commune_dvf(prix_median_maison, n_tx_*)` (agrégats only).
- [ ] Calcul `E` et `J` → `commune_pression` (sans `T`).
- [ ] **PMTiles** (tippecanoe) + **MapLibre** : choroplèthe `E`/enjeu + mise en avant des communes reclassées.
- [ ] **FastAPI** : `/communes/{insee}`, `/meta`.
- [ ] Tests : schéma connecteurs, contrats de données (volumétrie/nulls/SRS), `E∈[0,1]`.
- [ ] Bandeau de cadrage + `noindex`.
**Démonstration v0** : « où se concentre la valeur de bâti exposé, et quelles communes changent de classe pour les ventes 2026 ».

## v1 — « La boussole » (nowcast dynamique)
**But** : ajouter la tension hydrique du moment.
- [ ] Ingestion **SWI CatNat** + climatologie mensuelle/maille → `swi_maille(swi_anomalie)`.
- [ ] Ingestion **Hub'eau** (`stations` + `chroniques` pour climatologie au 1ᵉʳ run, puis `chroniques_tr` quotidien) → `piezo_ips`.
- [ ] Rattachement maille SWI ↔ commune et piézo ↔ commune (+ niveau de confiance).
- [ ] Calcul `T` puis `IP-RGA` (5 niveaux) ; curseur de date dans l'UI.
- [ ] **systemd timers** (quotidien nappes, mensuel SWI) ; `last_updated_*` exposés.
- [ ] Tests métier : monotonie (plus sec ⇒ score ≥), `E=0 ⇒ 0`, couverture nationale via SWI.
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
