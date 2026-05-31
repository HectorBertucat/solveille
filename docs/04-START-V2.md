# 04 — START V2 · Calibration `H` (GASPAR Cat-Nat sécheresse) + finitions v1.1 + MCP

> **À coller comme premier message d'une nouvelle session Claude Code** (contexte propre).
> Contexte dans le repo (`AGENTS.md`, `docs/`, code, commits) + ce fichier + la mémoire projet.

---

## Où on en est (v0 + v1.0 + v1.1 — TOUS LIVRÉS ✅, EN PROD)

Solveille est **en production nationale** sur <https://argile.hectorb.fr> (CI/CD GitHub Actions :
push `main` → lint+format+types+tests → déploiement SSH ; timers systemd pour les données).
**91 tests** verts. Tout est sur `main`.

- **v0 — carte de l'enjeu** : `E` (exposition argile RGA 2026 × vulnérabilité bâti) + `J`
  (valeur de bâti exposé, DVF) + flag reclassement 2026. ~1954 Md€ de bâti exposé national.
- **v1.0 — boussole SWI** : `T = dry_SWI` (anomalie d'humidité des sols, couverture 100 %),
  `IP-RGA = round(100·E·T^0.8)`, 5 niveaux (quantiles nationaux), **curseur de date** (108 mois
  2017→2025), sparkline. Timer SWI **mensuel**.
- **v1.1 — IPS Hub'eau (raffinement local)** : IPS piézométrique recalculé (méthode BRGM) —
  **NQT** `Φ⁻¹(rang Weibull)` → classes BRGM (affichées), **+ z plain** `(x−μ)/σ` → pilote `T` ;
  `T = (dry_SWI + w_ips·dry_IPS)/(1+w_ips)`, `w_ips = confiance·0.5` ; **représentativité
  spatiale** (station < 10 km, confiance décroissante). Refresh **quotidien incrémental**.
  En prod : **2809 stations**, 18,2 M mesures, **~19 400 communes** corroborées. `z_ips`/`dry_ips`/
  `ips_classe`/`confiance_t`/`last_updated_ips` exposés ; bloc **« Nappe (IPS local) »** sur la
  fiche front. Ex. Montpellier @2023-08 : nappe « Très bas » → pression « Très élevée ».

**Données déjà construites (prod + local)** : marts + PMTiles nationaux avec IPS. `data/` gitignored.
Timers actifs : `solveille-piezo.timer` (quotidien 05:56 UTC), `solveille-swi.timer` (mensuel).

**Décisions clés** : `docs/decisions.md` (ADR-001→018). `docs/metric.md` (E/T/J/H, IPS NQT+z plain).
`docs/data-sources.md` (§1-9, dont §5 Hub'eau et §6 GASPAR).

## Pièges techniques à NE PAS redécouvrir (durement appris en v1.1)

- **Source d'abord** : lancer le subagent `data-source-researcher` **AVANT de coder** tout
  nouveau connecteur (endpoint réel, schéma, filtrage, volumétrie, licence). Pour GASPAR :
  bien isoler les arrêtés **sécheresse géotechnique (RGA)** des autres aléas.
- **Anti-OOM national (VM 8 Go partagée, ~6,6 Go libres)** : tout build qui lit beaucoup de
  fichiers/lignes doit traiter **par lots + borner la mémoire DuckDB** (cf.
  `PIEZO_MEMORY_LIMIT="4GB"`, `MENSUEL_BATCH=200` dans `transform/piezo.py`, et le traitement
  **par département** dans `commune_swi`/`commune_rga`/`piezo_ips`). Un `read_json` glob sur des
  milliers de fichiers = OOM killer.
- **API tolérante au schéma** : toute **nouvelle colonne de mart** doit être lue **défensivement**
  dans `api/deps.py` (`fetch_commune`/`fetch_meta` ne sélectionnent que les colonnes présentes)
  — sinon, le code déployé **avant** le rebuild du mart fait des **500** en prod (code et données
  se déploient séparément : CI=code, timers=données).
- **Déploiement** : push `main` → CI déploie le **CODE** uniquement. Un **changement de schéma de
  mart** nécessite un **rebuild du mart sur la VM** pour peupler les nouvelles colonnes :
  `ssh root@178.104.144.205 'systemctl start solveille-piezo.service'` (idempotent, fetch caché).
- **Gate CI** : `ruff check` **ET** `ruff format --check`. Lancer **`make lint`** (les deux) avant
  push — sinon le format casse la CI.
- **Valider au national SOUS contrainte mémoire VM** avant de dire « fini » (le local a plus de RAM).
- **SSH ops** : `root@178.104.144.205` (Hector autorise lecture + rebuild idempotent).
- **Hub'eau** (rappel, déjà résolu) : `chroniques` ignore silencieusement les filtres géo →
  boucler par `code_bss` ; plafond `page×size ≤ 20000` ; fetch borné à 35 ans (`MAX_HISTORY_YEARS`).

---

## Objectif « toute la suite »

### A. Finitions v1.1 (petites, optionnelles — quick wins)
1. **BDLISA libre/captive** → pondérer `f_nappe` dans `metric.confiance_ips` (nappe libre = 1,
   captive = 0.5). Les `codes_bdlisa` sont **déjà stockés** dans `piezo_stations` ; il faut une
   table de passage BDLISA (Sandre/BRGM) type d'aquifère → joindre sur `codes_bdlisa`.
2. **chroniques_tr** (valeur courante intra-mois) : champ `niveau_eau_ngf`, horaire, ~3 mois ;
   en repli si la chronique qualifiée est en retard (fraîcheur nowcast).

### B. v2 — Calibration `H` (objectif principal, `docs/metric.md §H`, `data-sources.md §6`)
**But** : « les conditions actuelles correspondent à **X %** des situations passées ayant mené à
une reconnaissance Cat-Nat sécheresse **ici** » — lecture complémentaire, **indicative**.
1. **Connecteur `ingest/gaspar.py`** (`make fetch-gaspar` route déjà) : GASPAR data.gouv
   (`base-nationale-...-gaspar`) et/ou couche Géorisques « procédures administratives ». Filtrer
   les arrêtés **sécheresse RGA**, clé **code INSEE**, dates. Poli/idempotent (skill
   `opendata-connector`).
2. **`catnat_secheresse`** (staging) : par commune → liste des arrêtés sécheresse, **fréquence**,
   **dernier arrêté**, années de reconnaissance.
3. **Indice `H`** : croiser les **états `z_SWI`/`T` passés** (mart mensuel 2017→, voire plus loin
   via la climatologie SWI) avec les **années de reconnaissance**. Atout : le **SWI CatNat est
   l'indice officiel** d'instruction sécheresse → forte cohérence attendue. Estimer `H` = percentile
   empirique / fréquence (« le `T` actuel dépasse X % des situations ayant donné un arrêté »).
   **Pooling** régional/national probable (peu d'arrêtés par commune). Caveat : critères
   **administratifs** → indicatif. Effort `xhigh` ici.
4. **Brancher** `H` au mart (colonne(s) `catnat_freq`/`dernier_arrete`/`h_proba`) + **API**
   (lecture défensive !) + **front** (« X % des situations à risque ici » + historique arrêtés).
5. **Tests** : filtrage sécheresse correct, `H∈[0,1]`, cohérence (une commune souvent reconnue +
   conditions sèches ⇒ `H` élevé), monotonie en `T`.

### C. v2 — Serveur MCP (interface agent)
- Serveur **MCP** exposant la pression/fiche d'une commune (ou adresse) en langage naturel,
  réutilisant l'API `/communes/{insee}` (et un `/lookup` adresse→INSEE à ajouter si besoin).

### D. (Option) raffinement IRIS / pondération population INSEE.

---

## Plan attendu (plan mode, valider avant de coder)
1. **Plan mode** + `data-source-researcher` sur **GASPAR** (vérifier endpoint/schéma/filtrage
   sécheresse géotechnique/volumétrie/licence) **avant** d'écrire le connecteur.
2. Découper : connecteur GASPAR → `catnat_secheresse` → indice `H` (méthode + pooling) →
   branchement mart/API/front → tests → run Occitanie puis national.
3. **Première livraison v2 attendue** : GASPAR ingéré + `catnat_secheresse` contrôlée (arrêtés
   sécheresse par commune : fréquence + dernier arrêté), avec un test de cohérence — **avant** le
   calcul empirique de `H`.

## Garde-fous
EPSG:2154 ; sources polies/cachées/bornées ; indice **indicatif** (jamais un diagnostic ni un
conseil) ; DVF agrégats communaux + `noindex` ; **petits commits atomiques** (Conventional
Commits) ; subagents **read-only** (écritures au parent) ; rien de destructif ni `git push` sans
accord explicite ; **sources tracées** (`last_updated_*`) ; anti-OOM + API tolérante au schéma.

---

## Prompt de démarrage (à coller dans la nouvelle session)

> Tu reprends **Solveille** : **v0 + v1.0 (SWI) + v1.1 (IPS Hub'eau) sont LIVRÉS et EN PROD**
> national sur <https://argile.hectorb.fr> (91 tests, CI/CD active, timers piézo quotidien +
> SWI mensuel).
> 1. Lis `AGENTS.md`, `docs/04-START-V2.md` (ce fichier — recap + pièges + objectif), `docs/metric.md`
>    (§H), `docs/data-sources.md` (§6 GASPAR), `docs/decisions.md` (ADR-001→018), et la mémoire
>    projet. **Ne relis pas tout le code** : il est commité, stable, en prod.
> 2. Passe en **plan mode** et propose le plan de la **v2 « calibration H »** : vérif **GASPAR** via
>    `data-source-researcher` (AVANT de coder), connecteur `ingest/gaspar.py` (filtrer les arrêtés
>    **sécheresse géotechnique RGA**), table `catnat_secheresse` (fréquence + dernier arrêté par
>    commune), puis l'indice **`H`** empirique (croiser `z_SWI`/`T` passés ↔ années de reconnaissance,
>    pooling régional/national, `H∈[0,1]` indicatif), branchement mart/API/**front** (API **tolérante
>    au schéma** !), tests, run Occitanie puis national. **N'écris pas de code avant validation.**
> 3. **Pièges à respecter** (durement appris en v1.1) : **anti-OOM** (traitement par lots + mémoire
>    DuckDB bornée à 4 Go sur la VM 8 Go partagée) ; **API tolérante au schéma** (toute nouvelle
>    colonne de mart lue défensivement dans `api/deps.py`, sinon 500 en prod) ; **`make lint`** avant
>    push (la CI fait `ruff check` ET `ruff format --check`) ; un **changement de schéma mart** exige
>    un **rebuild du mart sur la VM** (`ssh root@178.104.144.205 'systemctl start solveille-piezo.service'`).
>    Effort `xhigh` sur le calcul de `H` ; subagents read-only ; pousse-moi si une hypothèse est fragile.
> **Première livraison v2 attendue** : GASPAR ingéré + `catnat_secheresse` contrôlée (arrêtés
> sécheresse par commune), avec un test de cohérence — avant le calcul de `H`.
>
> *Finitions v1.1 optionnelles si tu veux des quick wins d'abord : BDLISA libre/captive (pondérer
> `f_nappe`, `codes_bdlisa` déjà stockés) ; `chroniques_tr` (valeur courante intra-mois).*
