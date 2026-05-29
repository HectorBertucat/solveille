# 02 — START V1 · Reprise & démarrage de la « boussole dynamique »

> **À coller comme premier message d'une NOUVELLE session Claude Code** (contexte propre).
> Tout le contexte utile est dans le repo (`AGENTS.md`, `docs/`, code, commits) + ce fichier.

---

## Où on en est (v0 — LIVRÉ ✅)

Le v0 « carte de l'enjeu » est **complet, testé (43 tests), validé national** et commité (18 commits sur `main`). Le pipeline va du brut à une **carte MapLibre** servie par FastAPI.

**Ce qui existe et tourne :**
- Connecteurs `solveille/ingest/` : `admin_express`, `rga_2026`, `communes_bascule`, `insee_logement`, `fideli_epci`, `dvf` — idempotents, polis, EPSG:2154.
- Transforms `solveille/transform/` : `staging` (commune, rga, bascule, logement, epci_stock[+periode]), `commune_rga` (intersection → `E`), `downscale_fideli` (→ `n_maisons_exposees`), `commune_dvf` (prix), `mart` (`commune_pression`), `tiles` (PMTiles).
- Métrique `solveille/metric/ip_rga.py` : **E** (exposition) et **J** (valeur de bâti exposé). **T et `ip_rga_score`/`niveau` = NULL en v0** (← c'est l'objet du v1).
- API `solveille/api/` : `/communes/{insee}`, `/meta`, `noindex`, sert aussi le front + PMTiles.
- Front `front/` : choroplèthe E + communes reclassées 2026 + fiche + cadrage.

**Données déjà construites localement** (`data/`, gitignored) : mart national + PMTiles. Pour voir : `make api` → http://localhost:8000. **Ne pas re-fetch/re-build sauf besoin** (RGA/DVF national ~10 min).

**Chiffres v0 :** 34 746 communes · ~1 954 Md€ de bâti exposé · ~10,3 M maisons exposées · 133 communes reclassées 2026.

**Décisions clés (voir `docs/decisions.md` ADR-011→014, `docs/metric.md`) :**
- ADMIN EXPRESS = GPKG v4 via `data.geopf.fr` ; RGA 2026 via **FeatureServer ArcGIS** (repli, dissous par dept×niveau, simplifié 25 m) ; INSEE logement ajouté en v0 ; descente Fideli = stock INSEE × exposition ; 3 millésimes COG (2026/2025/2021) réconciliés par anti-jointures.
- `E = clamp01(0.6·part_alea_moyen_fort + 0.4·part_maisons_vulnerables)` ; **gating** : pas d'argile ⇒ E=0.

## Pièges techniques à NE PAS redécouvrir
- **Reprojection 4326→2154 : `ST_Transform(..., always_xy := true)` OBLIGATOIRE** (sinon axes inversés).
- Géométries persistées en **WKB** (`geom_wkb` BLOB, convention SRID 2154) ; recharger via `ST_GeomFromWKB`.
- **Mémoire (VM 8 Go)** : traiter les intersections/agrégats **par département en boucle** + simplifier les gros polygones ; ça a évité l'OOM en v0.
- `read_csv` DVF : `quote='"'` (virgules dans adresses). INSEE/Fideli : séparateur `;`.
- Subagents read-only : `data-source-researcher` (vérifier source AVANT le connecteur), `geo-duckdb-reviewer` (SQL spatial), `metric-validator` (indice). Skills : `opendata-connector`, `duckdb-spatial`.

---

## Objectif v1 — « la boussole » (nowcast dynamique)

Ajouter la **tension hydrique T** pour rendre la carte **dynamique dans le temps** et calculer le vrai **IP-RGA** = `100·E·Tᵞ` sur 5 niveaux. C'est l'insight unique du projet (dynamique × statique).

**Plan attendu (à proposer en plan mode, valider avant de coder) :**
1. **Vérifier les 2 sources** via `data-source-researcher` (AVANT de coder) :
   - **SWI CatNat** (Météo-France / meteo.data.gouv) : CSV mensuel, maille 8 km (x/y L93), historique depuis 1960 → endpoint réel, format, colonnes, volumétrie, fenêtres décennales.
   - **Hub'eau Piézométrie** (`hubeau.eaufrance.fr/api/v1/niveaux_nappes/`) : `stations`, `chroniques` (climato 1er run), `chroniques_tr` (quotidien) → pagination, `code_bss`, bornage département.
2. **Connecteurs** `ingest/swi_catnat.py`, `ingest/hubeau_piezo.py` (cibles `make fetch-swi`/`fetch-piezo` déjà dans le Makefile).
3. **Staging + climatologie** (skill `duckdb-spatial`) :
   - `swi_maille(maille, geom_2154, date_mois, swi, swi_anomalie)` + climatologie **mensuelle par maille** → `z_SWI` standardisé (anomalie du **même mois**, pas tous mois mélangés).
   - `piezo_ips(code_bss, date_mois, ips, ...)` : IPS = position du niveau courant dans la distribution mensuelle historique.
4. **Rattachement** maille SWI ↔ commune et piézo ↔ commune (moyenne pondérée par aire / plus proche) + **niveau de confiance** (IPS = raffinement local ; SWI = signal universel).
5. **Calcul T** (`metric/ip_rga.py`) : `dry_SWI = σ(−z_SWI)`, `dry_IPS = σ(−z_IPS)`, `T = w_swi·dry_SWI + w_ips·dry_IPS` (T = dry_SWI si IPS absent). Puis **`ip_rga_score = round(100·E·Tᵞ)`**, `ip_rga_niveau` (5 niveaux, quantiles nationaux documentés).
6. **Mart + carte** : `commune_pression` par mois, **curseur de date** dans le front ; PMTiles par date ou attributs temporels.
7. **systemd timers** (mensuel SWI, quotidien nappes) ; `last_updated_*` exposés.
8. **Tests métier** : monotonie (plus sec ⇒ score ≥ à E fixe), `E=0 ⇒ score=0`, couverture 100 % communes via SWI, cohérence temporelle (mois sec connu > mois humide).

**Garde-fous (rappel) :** EPSG:2154 ; connecteurs polis (cache + bornage, pas de matraquage Hub'eau/meteo.data.gouv) ; indice indicatif ; petits commits atomiques ; rien de destructif sans accord. **Itérer Occitanie puis national.**

---

## Prompt de démarrage (à coller dans la nouvelle session)

> Tu reprends **Solveille** après la livraison du **v0** (complet, voir `docs/02-START-V1.md`).
> 1. Lis `AGENTS.md`, `docs/02-START-V1.md`, `docs/metric.md`, `docs/decisions.md`, `docs/data-sources.md` (§4 SWI, §5 Hub'eau). Ne relis pas tout le code : il est commité et stable.
> 2. Passe en **plan mode** et propose le plan d'exécution du **v1 « boussole »** : vérif sources (SWI, Hub'eau) via `data-source-researcher`, connecteurs, staging + **climatologie** (anomalie SWI standardisée par mois, IPS), rattachement maille/piézo↔commune, calcul **T** puis **IP-RGA = 100·E·Tᵞ** (5 niveaux), curseur de date, systemd timers, tests de **monotonie**. N'écris pas de code avant validation.
> 3. Effort élevé sur la climatologie/anomalies ; subagents parallèles pour les 2 sources puis auto-vérification. Pousse-moi si une hypothèse est fragile.
> Première livraison v1 attendue : SWI CatNat ingéré → `z_SWI` par commune contrôlé, avec test de cohérence temporelle.
