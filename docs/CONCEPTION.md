# Solveille — Conception (spec maître)

> **Solveille** · *la veille des sols argileux.*
> Nowcast, **par commune** et **à l'échelle nationale**, de la **pression sécheresse–argiles (RGA)** en cours, croisée avec le **bâti exposé** et la **valeur foncière à risque**, et **calibrée sur l'historique des reconnaissances Cat-Nat sécheresse**.

Projet démo / portfolio data-engineering. Build assisté par Claude Code (Opus 4.8). Déploiement : 1 VM Ubuntu (4 vCPU / 8 Go RAM / 80 Go / 20 To out).

Documents liés : [`architecture.md`](architecture.md) · [`data-sources.md`](data-sources.md) · [`metric.md`](metric.md) · [`decisions.md`](decisions.md) · [`roadmap.md`](roadmap.md).

---

## 1. Pourquoi (le problème)
Le **retrait-gonflement des argiles (RGA)** est devenu la **1ʳᵉ cause de sinistre** sur les maisons individuelles : les sols argileux gonflent avec l'humidité et se rétractent en sécheresse, fissurant les fondations. ~**10,5 M** de maisons (sur 19,4 M) sont en zone d'aléa **moyen/fort**, et un **nouveau zonage 2026** (arrêté du 9 janv. 2026) s'applique aux ventes depuis le 1ᵉʳ juillet 2026. Le sud-ouest (dont **Toulouse/Occitanie**) est très exposé.

## 2. L'insight unique (vérifié : personne ne le package)
- Les outils **statiques** disent « suis-je sur de l'argile ? » (Géorisques, Callendar « Risque Maison Climat RGA », TerraStab).
- Les outils **dynamiques** disent « les nappes sont-elles basses ? » (BRGM MétéEAU Nappes, Info-Sécheresse).
- **Aucun** ne **fusionne** : *moteur dynamique (sécheresse en cours) × exposition argile × enjeu bâti/valeur × calibrage sur les sinistres passés*, au niveau commune, rafraîchi régulièrement. **C'est l'angle de Solveille.**

## 3. Ce que ce N'EST PAS (cadrage, à afficher dans l'UI)
- **Pas un diagnostic géotechnique** : à l'échelle d'un bâtiment, le RGA dépend de la géologie locale, de la profondeur des fondations, de la végétation, des fuites… Solveille produit un **indice territorial**, pas une prédiction de fissures par maison.
- **Pas un conseil** d'achat, d'assurance ou d'expertise. Indicateur **indicatif**, méthode et sources ouvertes.

## 4. Le produit
- **Carte choroplèthe nationale** (maille **commune**) coloriée par le niveau de **pression RGA actuelle** (5 niveaux), avec curseur de date (composante dynamique).
- **Fiche commune** : pression du moment, exposition argile (part de surface en aléa moyen/fort + classe dominante), stock de maisons individuelles exposées, **ordre de grandeur de valeur de bâti exposé**, historique des arrêtés Cat-Nat sécheresse, et **flag « commune reclassée au 1ᵉʳ juillet 2026 »**.
- **Lecture calibrée (v2)** : « les conditions actuelles ressemblent à X % des situations qui ont mené à une reconnaissance Cat-Nat ici ».
- **API** publique + **serveur MCP (v2)** pour interroger la pression d'une commune en langage naturel.

## 5. L'indicateur cœur — IP-RGA
Détail complet et formules dans [`metric.md`](metric.md). En une phrase : `IP-RGA = f(Exposition argile, Tension hydrique)`, avec :
- **Exposition `E`** (statique) : part de surface/bâti en aléa moyen+fort, pondérée vulnérabilité du bâti.
- **Tension hydrique `T`** (dynamique, anomalies **standardisées** donc comparables) : **SWI CatNat** (Météo-France, maille 8 km — *la variable officielle d'éligibilité Cat-Nat sécheresse*) + **IPS** (Indicateur Piézométrique Standardisé calculé depuis Hub'eau, cohérent avec SPI/SSWI).
- **Enjeu `J`** (statique) : nb de maisons exposées (Fideli/EPCI) × prix médian maison (DVF) → valeur de bâti exposé ; + transactions récentes en zone exposée.
- **Calibration `H`** (v2) : relation empirique entre états SWI/IPS passés et arrêtés Cat-Nat (GASPAR).

La formule est **transparente et documentée** (pas de boîte noire).

## 6. Sources de données
Vue d'ensemble ci-dessous ; playbook d'ingestion détaillé (endpoints, paramètres, SRS, pièges) dans [`data-sources.md`](data-sources.md).

| Source | Rôle | Accès | Cadence |
|---|---|---|---|
| RGA 2026 (Géorisques/BRGM) | Exposition `E` + couche carte | SHP L93 / PMTiles | maj zonage |
| Communes basculant RGA 2026 | Flag reclassement | CSV (data.gouv) | statique |
| Exposition maisons / RGA par EPCI (Fideli) | Stock `J` | CSV (data.gouv) | annuel |
| SWI CatNat (Météo-France) | Anomalie sol `T` | CSV (meteo.data.gouv) | mensuel (depuis 1960) |
| Hub'eau Piézométrie | IPS nappes `T` | API JSON | horaire (`chroniques_tr`) / ADES quotidien |
| GASPAR Cat-Nat | Calibration `H` | CSV (data.gouv / Géorisques) | maj < 30 j après J.O. |
| DVF géolocalisé | Prix médian `J` | CSV/an (data.gouv) | semestriel (avril/oct.) |
| ADMIN EXPRESS (IGN) | Géométries communes | SHP/GeoJSON | annuel |
| INSEE (pop, option) | Pondération population | CSV | annuel |

## 7. Architecture (résumé)
Détail dans [`architecture.md`](architecture.md).
`Ingestion (httpx, planifiée, idempotente) → data/raw → DuckDB+spatial (reproj EPSG:2154, jointures, fenêtres percentiles) → data/marts (commune_pression) → PMTiles (statique) + FastAPI → MapLibre ; MCP en v2.`
Principe : **fichiers Parquet + DuckDB** (pas de serveur DB) pour tenir dans 8 Go de RAM.

## 8. Modèle de données (tables clés)
- `commune(insee, nom, dept, epci, geom_2154, basculement_2026)`
- `commune_rga(insee, part_alea_moyen, part_alea_fort, classe_dominante)`
- `epci_stock(epci, niveau_rga, periode_construction, n_maisons_exposees)`
- `commune_dvf(insee, prix_median_maison_eur_m2, n_tx_maison_12m, n_tx_zone_exposee_12m)`
- `swi_maille(maille, geom_2154, date_mois, swi, swi_anomalie)` + climatologie mensuelle/maille
- `piezo_station(code_bss, geom_2154, insee, masse_eau)` · `piezo_ips(code_bss, date_mois, ips, valeur_courante)`
- `catnat_secheresse(insee, date_arrete, n_cumule)`
- `commune_pression(insee, date, E, T, J, ip_rga_score, ip_rga_niveau, valeur_bati_exposee_eur, last_updated_*)` ← **table servie**

## 9. Surface API (esquisse)
- `GET /communes/{insee}` → fiche complète (pression, expo, stock, valeur, historique, flag 2026).
- `GET /pression?date=YYYY-MM` → niveaux par commune (pour la carte, sinon servis via PMTiles).
- `GET /lookup?lat=&lon=` → commune + pression (réutilisable par le MCP).
- `GET /meta` → dates `last_updated_*` par source. Réponses documentées (OpenAPI auto FastAPI).

## 10. Front (esquisse)
- Carte MapLibre + PMTiles (choroplèthe commune), légende 5 niveaux, curseur de date, recherche commune.
- Page fiche commune. Bandeau de cadrage « indice indicatif, pas un diagnostic ». Mentions sources + dates. `noindex` global (contrainte DVF).

## 11. Stratégie de tests & qualité
- **Tests unitaires** : parsing/validation de chaque connecteur (schéma attendu, types, SRS).
- **Tests de données** (contrats) : volumétrie plausible, taux de nulls borné, couverture communes, cohérence géo (communes ∈ France, mailles SWI couvrantes).
- **Tests métier** : `IP-RGA` borné [0,100], monotonie (plus sec ⇒ pression ≥), reproductibilité.
- **Validation IPS/anomalies** sur cas connus (ex. été sec documenté vs hiver humide).
- Lint/format **ruff**, typage **mypy**. CI légère (GitHub Actions) : lint + tests sur un échantillon.

## 12. Observabilité & fraîcheur
- Chaque source écrit un manifeste `data/raw/<source>/_meta.json` (date, version, hash, nb lignes).
- `last_updated_*` propagés jusqu'à l'UI. Logs structurés des ingestions. Alerte simple si une source n'a pas été rafraîchie dans sa fenêtre attendue.

## 13. Déploiement (VM)
- Service FastAPI (uvicorn) derrière un reverse proxy (Caddy/Nginx, TLS auto).
- PMTiles servis en statique (range requests). Ingestions via **systemd timers** (quotidien : Hub'eau ; mensuel : SWI ; semestriel/à-l'événement : RGA, DVF, GASPAR).
- Budgets : stockage < 80 Go (DVF→Parquet ; SWI/piézo agrégés) ; RAM 8 Go suffisante avec DuckDB en streaming ; egress trivial (20 To).

## 14. Sécurité & cadre légal
- **DVF** : agrégats communaux uniquement, pas de réidentification, **pas d'indexation moteurs** (`noindex`/robots).
- Licences : Étalab/Licence Ouverte 2.0 (RGA, SWI, GASPAR, DVF), ODbL pour certaines couches — afficher attributions.
- Pas de secret en clair ; config via `.env`. Pas de PII collectée côté utilisateur.

## 15. Valeur portfolio (CV)
Ingestion multi-sources hétérogènes (API + fichiers, données « pas propres »), géo-traitement DuckDB (reprojection, jointures spatiales), normalisation de séries temporelles (IPS / anomalies climatologiques), **validation contre des résultats réels** (arrêtés Cat-Nat), pipeline planifié/idempotent, packaging carto (PMTiles/MapLibre) + API (FastAPI) + agent (MCP), le tout sur une VM modeste (maîtrise des contraintes ressources). Thème climate-risk, daté (zonage 2026) et local.

## 16. Glossaire
- **RGA** : retrait-gonflement des argiles. **Cat-Nat** : reconnaissance de l'état de catastrophe naturelle. **SWI** : Soil Wetness Index (humidité des sols). **IPS** : Indicateur Piézométrique Standardisé (état des nappes vs normale). **DVF** : Demandes de Valeurs Foncières. **EPCI** : intercommunalité. **PMTiles** : tuiles vectorielles dans un fichier unique.
