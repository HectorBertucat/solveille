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

### v1.0 — SWI (la dynamique nationale) ✅ **Livré**
- [x] Ingestion **SWI CatNat** (CDN data.gouv `…-catnat`) → `swi_maille`, `swi_grille`.
- [x] Climatologie mensuelle/maille (tout l'historique 1960→) → anomalie standardisée `z_SWI` (`swi_anomalie`).
- [x] Rattachement maille SWI ↔ commune (carré 8 km ∩ commune, pondéré par aire) → `commune_swi` (couverture 100 %).
- [x] Calcul `T = dry_SWI` puis `IP-RGA = round(100·E·T^0.8)` (5 niveaux, quantiles nationaux) → `commune_pression_mensuel` + statique `*_latest`.
- [x] **Curseur de date** dans l'UI (niveau IP-RGA par mois en attribut de tuile `n_AAAAMM`) + sparkline de pression dans la fiche.
- [x] **systemd timer** mensuel SWI (`deploy/`) ; `last_updated_swi` exposé.
- [x] Tests métier : monotonie, `E=0 ⇒ 0`, **couverture nationale 100 % via SWI**, cohérence temporelle (mois sec > mois humide). **71 tests.**
**Chiffres v1.0** : 34 746 communes × 108 mois (2017-01→2025-12) ; seuils niveaux [24,35,47,61] ; août 2022 (sécheresse) ressort en pression nationale max (z̄ −1.44).

### v1.1 — IPS Hub'eau (raffinement local) ✅ **Livré (en prod)**
- [x] Ingestion **Hub'eau** (`stations` par dept → `code_bss` ; `chroniques` climatologie, fetch incrémental borné 35 ans) → `piezo_ips` (recalcul IPS : **NQT** classes BRGM **+ z plain** pour `T`).
- [x] Rattachement piézo ↔ commune (point-dans-commune + repli INSEE + **représentativité < 10 km**) avec **niveau de confiance** ; pondération `w_ips = confiance·0.5` dans `T`.
- [x] **systemd timer** quotidien nappes (refresh incrémental) ; `z_ips`/`dry_ips`/`ips_classe`/`confiance_t`/`last_updated_ips` exposés ; bloc « Nappe (IPS local) » sur la fiche front.
- [ ] (option) BDLISA libre/captive pour `f_nappe` ; `chroniques_tr` intra-mois.
**Chiffres v1.1** : 2809 stations, 18,2 M mesures, ~19 400 communes corroborées (national). ADR-018.
**Démonstration v1** : carte de pression qui évolue dans le temps, par commune, affinée par les nappes là où une station est représentative. ✅ **Livré.**

## v2 — « Calibration + agent »
**But** : profondeur analytique et interface agent.
- [x] **GASPAR** sécheresse → `catnat_secheresse` ; relation empirique SWI ↔ périodes de reconnaissance → `H` = CDF empirique « X % des situations ayant mené à un arrêté ici » (indicatif). Branché mart/API/front (bloc « Calibration historique »), timer hebdo, **en prod**. ADR-019, `metric.md §H`. ✅ **Livré.**
- [ ] Analyse de tendance (rapprochement du seuil) — *non commencé*.
- [ ] **Serveur MCP** : interroger la pression d'une commune/adresse en langage naturel (réutilise `/lookup`) — *non commencé*.
- [ ] (Option) raffinement **IRIS** ; pondération **population INSEE** — *non commencé*.
- [ ] (Option, hérité v1.1) BDLISA libre/captive pour `f_nappe` ; `chroniques_tr` intra-mois.
**Chiffres v2** : 47 576 arrêtés sécheresse (1990→2025), 14 229 communes ; `H` calibré sur le SWI historique, pooling départemental. Build national ~3 s.
**Démonstration v2** : « les conditions actuelles ressemblent à X % des situations passées à risque ici » (✅ fait) + requêtes en langage naturel (MCP, à venir).

## Transverse (tout du long)
- Lint **ruff** + typage **mypy** + **pytest** ; CI légère (lint+tests sur échantillon).
- Manifestes `_meta.json` par source ; logs structurés ; doc à jour.
- Commits atomiques (Conventional Commits) ; rien de destructif sans accord.
