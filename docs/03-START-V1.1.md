# 03 — START V1.1 · Hub'eau / IPS (raffinement local de la tension hydrique)

> **À coller comme premier message d'une nouvelle session Claude Code** (contexte propre).
> Contexte dans le repo (`AGENTS.md`, `docs/`, code, commits) + ce fichier.

---

## Où on en est (v1.0 — LIVRÉ ✅)

La **boussole SWI** est complète, testée (**71 tests**), validée national, commitée. Pipeline
brut → carte de **pression IP-RGA mensuelle** avec **curseur de date**.

**Ce qui tourne (v1.0) :**
- Connecteur `ingest/swi_catnat.py` (data.gouv `…-catnat`, 7 CSV.gz + grille, idempotent).
- Staging `transform/staging.py` : `swi_grille`, `swi_maille`, `swi_clim` (climatologie 1960→),
  `swi_anomalie` (z_SWI standardisé par maille×mois calendaire, fenêtre 2017→).
- `transform/commune_swi.py` : carré 8 km ∩ commune pondéré par aire (par dept, repli îles) →
  `commune_swi` (z_SWI communal, **couverture 100 %**).
- `metric/ip_rga.py` : `sigma`, `dry_intensity`, `tension_t` (T=dry_SWI en v1.0), `ip_rga_score`,
  `niveau_*`. Réplique SQL dans `mart.py`.
- Marts `transform/mart.py` : **`commune_pression_mensuel`** (1 ligne/(insee,mois)) + statique
  `commune_pression` (dernier mois fusionné) ; seuils 5 niveaux = quantiles nationaux (E>0) →
  `marts/seuils_niveaux.json`.
- API : `/communes/{insee}?mois=AAAA-MM`, `/communes/{insee}/serie`, `/meta` (last_updated_swi,
  mois_disponibles, seuils_niveaux).
- Tuiles `tiles.py` : pivot mensuel → attribut `n_AAAAMM` (niveau 0-5) par mois (PMTiles ~40 Mo).
- Front : curseur de date, choroplèthe par niveau du mois, sparkline de pression, caveats.
- Déploiement : `deploy/systemd/solveille-swi.{service,timer}` (mensuel) + `deploy/run-refresh.sh`.

**Données déjà construites localement** (`data/`, gitignored) : staging SWI + marts + PMTiles
nationaux. `make api` → http://localhost:8000. **Ne pas re-fetch/re-build sauf besoin** (RGA/DVF
national ~10 min ; SWI ~30 Mo, rapide).

**Chiffres v1.0 :** 34 746 communes × 108 mois (2017-01→2025-12) ; seuils [24,35,47,61] ;
août 2022 (sécheresse) = pression nationale max (z̄ −1.44), 2024 humide.

**Prod :** déployé sur la VM (Ubuntu 24.04, partagée) — `solveille-api.service` (uvicorn 127.0.0.1:8001),
Caddy `:8083`, Cloudflare Tunnel → **<https://argile.hectorb.fr>**. CI/CD GitHub Actions (`.github/workflows/ci.yml`) :
push `main` → lint+types+tests → déploiement SSH (git reset + uv sync + restart). Timer `solveille-swi`
mensuel (refresh SWI léger `make build-swi`). Secrets `DEPLOY_*` posés. Voir `deploy/README.md`.

**Décisions clés v1 (ADR-015/016/017, `docs/metric.md`) :**
- SWI via CDN data.gouv (PAS le portail JS) ; `LAMBX/LAMBY` déjà en L93 ; grille pour la géométrie.
- Mart **temporel** split statique/mensuel ; niveau-par-mois en attribut de tuile pour le curseur.
- `T = dry_SWI = sigma(-z_SWI)` ; `IP-RGA = round(100·E·T^0.8)` ; 5 niveaux par quantiles nationaux.
- Climatologie sur **tout l'historique 1960→** ; SWI = moyenne glissante 3 mois (nowcast lissé).

## Pièges techniques à NE PAS redécouvrir (en plus de ceux de v0)
- **Schéma `z_ips`/`dry_ips` déjà réservés (NULL) dans `commune_pression_mensuel`** → brancher
  l'IPS sans nouvelle migration ; `confiance_t` codé `1.0` en v1.0 (à moduler en v1.1).
- **Arrondi du score** : `ip_rga_score` (Python) aligné *half-up* sur `round()` DuckDB (parité SQL).
- **Hub'eau** (cf. `docs/data-sources.md §5`, vérifié) : `chroniques` n'accepte **aucun filtre géo**
  → lister les `code_bss` via `stations?code_departement=` d'abord ; plafond dur `page×size ≤ 20000` ;
  coords WGS84 → reproj 2154 ; **NGF vs profondeur** (signe !) ; champ `niveau_nappe_eau`
  (`chroniques`) ↔ `niveau_eau_ngf` (`chroniques_tr`) ; **aucun endpoint ne sert l'IPS** (recalcul).

---

## Objectif v1.1 — IPS Hub'eau (raffinement local)

Ajouter l'**IPS piézométrique** comme second signal de T là où une station représentative existe
(`T = w_swi·dry_SWI + w_ips·dry_IPS`), avec un **niveau de confiance** par commune. Le SWI reste le
signal universel (couverture 100 %) ; l'IPS affine localement.

**Plan attendu (plan mode, valider avant de coder) :**
1. Connecteurs `ingest/hubeau_piezo.py` : (a) `stations` par dept → `code_bss` (filtrer
   `date_debut_mesure ≤ today−15 ans`) ; (b) `chroniques` (climatologie, paginé par fenêtres de
   dates < 20000) ; (c) `chroniques_tr` (valeur courante quotidienne). Polis, idempotents.
2. Staging + **IPS recalculé** (méthode BRGM : niveaux mensuels → CDF empirique par mois → quantile
   normal ; classes ou z standardisé) → `piezo_ips(code_bss, date_mois, z_ips/ips, confiance)`.
3. Rattachement **piézo ↔ commune** (station représentative / plus proche, nappe libre privilégiée)
   + **niveau de confiance** (présence/qualité/historique).
4. Brancher dans `metric/ip_rga.tension_t` (`w_ips`) et la réplique SQL du mensuel (remplir
   `z_ips`/`dry_ips`/`confiance_t`) ; recalcul mart + tuiles.
5. **systemd timer quotidien** nappes (`deploy/solveille-piezo.timer`).
6. Tests : IPS standardisé, `T=dry_SWI` si pas de station, monotonie préservée, confiance exposée.

**Garde-fous :** EPSG:2154 ; Hub'eau poli (cache + bornage dept, pas de matraquage, plafond 20000) ;
indice indicatif ; petits commits atomiques ; rien de destructif sans accord. Itérer Occitanie puis national.

---

## Prompt de démarrage (à coller dans la nouvelle session)

> Tu reprends **Solveille** après la livraison du **v1.0** (boussole SWI, complète + testée + **déployée en prod sur <https://argile.hectorb.fr>**, CI/CD GitHub Actions active).
> 1. Lis `AGENTS.md`, `docs/03-START-V1.1.md`, `docs/metric.md`, `docs/data-sources.md` (§5 Hub'eau), `docs/decisions.md` (ADR-015→017). Ne relis pas tout le code : il est commité et stable.
> 2. Passe en **plan mode** et propose le plan du **v1.1 « IPS Hub'eau »** : vérif source Hub'eau via `data-source-researcher` (AVANT de coder), connecteurs `stations`/`chroniques`/`chroniques_tr`, **recalcul IPS** (climatologie mensuelle par `code_bss`, classes BRGM, ≥15 ans), rattachement piézo↔commune + **niveau de confiance**, branchement `w_ips` dans `T` (les colonnes `z_ips`/`dry_ips` du mart mensuel sont déjà réservées, NULL), **timer quotidien** nappes, tests (IPS standardisé, `T=dry_SWI` si pas de station, monotonie préservée). N'écris pas de code avant validation.
> 3. Effort élevé sur l'IPS/climatologie ; subagents read-only (`data-source-researcher`, `geo-duckdb-reviewer`, `metric-validator`) ; pousse-moi si une hypothèse est fragile. Itère Occitanie puis national.
> Première livraison v1.1 attendue : un IPS par `code_bss` contrôlé (centré-réduit, classes BRGM) rattaché aux communes, avec un test de cohérence.
