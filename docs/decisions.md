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
