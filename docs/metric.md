# Indice IP-RGA — définition détaillée

Objectif : un score **par commune** et **par mois** (rafraîchi quotidiennement via la nappe) qui combine l'**exposition** structurelle à l'argile et la **tension hydrique** du moment, pour signaler les communes sous **pression RGA anormale**. Transparent et documenté — **pas de boîte noire**, **pas un diagnostic bâtiment**.

## Composantes

### E — Exposition argile (statique, 0–1)
Part de surface communale (et/ou de bâti) en aléa **moyen + fort** du zonage RGA 2026, pondérée par la **vulnérabilité du bâti** :
```
E = clamp01( w_surface * part_alea_moyen_fort
           + w_bati    * part_maisons_vulnerables )
```
- `part_alea_moyen_fort` : aire(aléa≥moyen ∩ commune) / aire(commune) — via DuckDB spatial.
- `part_maisons_vulnerables` : part des maisons exposées construites avant les règles limitant le risque (proxy période de construction Fideli ; les maisons anciennes sont plus sensibles).
- Poids `w_*` documentés et ajustables (défaut : 0,6 / 0,4).
- **Gating (implémentation)** : si `part_alea_moyen_fort = 0`, alors **`E = 0`** quelle que soit la vulnérabilité. `part_maisons_vulnerables` est la part *des maisons exposées* : sans aléa moyen+fort, elle n'a pas de support (sinon la vulnérabilité EPCI fuirait sur une commune non argileuse, ex. Paris). Garantit `E=0 ⇒ score=0`.
- **Vulnérabilité indisponible** (EPCI Fideli non apparié au COG commune) : `E = clamp01(part_alea_moyen_fort)` (exposition surfacique seule) plutôt que de sous-pondérer.
- `E = 0` peut signifier « pas d'argile » **ou** « hors couverture RGA » (ex. Paris) : l'UI s'appuie sur `has_rga_coverage` pour ne pas afficher un faux « 0 mesuré ».

### T — Tension hydrique (dynamique, anomalie standardisée)
Deux signaux **standardisés** (moyenne 0, écart-type 1 sur la climatologie locale), donc directement comparables et cohérents entre eux :
- **`z_SWI`** : anomalie standardisée du SWI CatNat de la maille (vs sa distribution **du même mois calendaire** sur l'historique). `z_SWI = (swi_t − μ_maille,mois)/σ_maille,mois`. Sécheresse ⇒ `z_SWI` négatif.
- **`z_IPS`** (v1.1, Hub'eau/ADES) : niveau de nappe (cote **NGF**) rapporté à la distribution du **même mois calendaire** sur l'historique de la station (≥ 15 ans). NGF haut = nappe haute = humide ⇒ sécheresse ⇒ `z_IPS` négatif. **Deux standardisations stockées** (ADR-018) : `z_ips` **plain** `(niveau − μ_mois)/σ_mois` **pilote `T`** (même méthode que `z_SWI`), et `ips_nqt = Φ⁻¹(rang_Weibull)` (**NQT**, méthode BRGM, N(0,1) par construction) **pilote la classe BRGM** affichée. Les deux sont monotones croissants dans le niveau ⇒ même sens sec/humide.

On convertit en intensité de sécheresse `[0,1]` (1 = très sec) via la **logistique**, puis on combine en **moyenne pondérée** (normalisée — un mois normal reste à 0.5 quel que soit le poids) :
```
sigma(x) = 1 / (1 + exp(-x))
dry_SWI  = sigma(-GAIN * z_SWI)            # z_SWI<0 (sec) ⇒ dry_SWI→1 ; z=0 ⇒ 0.5
dry_IPS  = sigma(-GAIN * z_IPS)            # z_IPS = z plain (pas la NQT)
T = (w_swi * dry_SWI + w_ips * dry_IPS) / (w_swi + w_ips)   # IPS indisponible (w_ips=0) ⇒ T = dry_SWI
```
- **Constantes** (documentées, ajustables) : `GAIN = 1.0` (pente ; `z=−1⇒dry≈0.73`, `z=−2⇒0.88`), `w_swi = 1`, **`w_ips = confiance · W_IPS_MAX`** (`W_IPS_MAX = 0.5` ⇒ SWI dominant ; à confiance max l'IPS pèse la moitié du SWI). `confiance ∈ [0,1] = clamp01(f_hist · f_nappe · f_repr)` : `f_hist` 0 si <15 ans, **plancher 0.4 à 15 ans** → 1.0 à 30 ans (un historique de 15 ans est déjà exploitable) ; `f_nappe` (libre 1 / captive 0.5 / inconnu 0.7, BDLISA, M2) ; `f_repr` (1 commune-hôte ; décroît avec la distance, M2). **`confiance = 0` (pas de station / <15 ans) ⇒ `w_ips = 0` ⇒ `T = dry_SWI`.**
- **Classes BRGM de l'IPS** (`ips_nqt`) : 7 classes aux seuils standard-normaux `[−1.282, −0.842, −0.253, +0.253, +0.842, +1.282]` (= quantiles N(0,1) des percentiles 10/20/40/60/80/90 %) — *Très bas (sec) … Très haut (humide)*. La NQT garantit que ces seuils tombent exactement sur les percentiles mensuels, ce qu'un z plain ne ferait pas sur une distribution de niveaux non-gaussienne — d'où le double calcul (z plain pour `T`, NQT pour la classe).
- **Mois « normal » ⇒ `T = 0.5`** (z=0) : T module E sans l'annuler — la pression reste à mi-échelle hors anomalie. Un mois sec pousse T→1, un mois humide T→0.
- **Lissage** : le SWI CatNat est déjà une **moyenne glissante 3 mois** (le nowcast est donc lissé, pas instantané — à afficher).
- **Climatologie** : μ/σ calculés par (maille, mois calendaire) sur **tout l'historique disponible (1960→)** pour maximiser l'échantillon et la robustesse de σ. *Caveat assumé* : la tendance climatique rend les anomalies récentes légèrement plus « sèches » vs une normale longue — cohérent avec « pression vs normale historique ». Période de référence **paramétrable** (ex. normale 1991-2020) si besoin. σ≈0 (maille quasi constante un mois) ⇒ `z_SWI` NULL+flag. **L'IPS suit la même logique** (climatologie par `code_bss`×mois calendaire sur tout l'historique de la station, paramétrable ; BRGM propose 1981-2010), avec deux caveats v1.1 : (a) la **NQT suppose la stationnarité** de la distribution mensuelle — une nappe en déclin tendanciel paraîtra plus sèche, comme le SWI ; (b) **asymétrie méthodologique assumée** — `z_SWI` est un z plain, `z_IPS` pilotant `T` aussi (cohérence), mais la **classe** IPS vient d'une NQT (N(0,1) exact).
- **SWI = signal universel** (grille 8 km, couverture totale, variable officielle Cat-Nat). **IPS = raffinement local** là où une station représentative existe → pondération `w_ips` réduite (ou nulle) sans station fiable. Exposer un niveau de **confiance** par commune (présence/qualité de l'IPS).

### J — Enjeu (statique, € et stock)
```
valeur_bati_exposee_eur ≈ n_maisons_exposees(commune) * surface_mediane * prix_median_maison(commune, DVF)
```
+ `n_tx_zone_exposee_12m` (transactions récentes de maisons en zone exposée → acheteurs potentiellement non avertis, d'autant que le zonage 2026 s'applique aux ventes depuis juillet 2026). `J` sert à **prioriser/illustrer**, pas à gonfler le score de pression.

### H — Calibration historique (v2) — `h_proba ∈ [0,1]`
But : « la sécheresse **actuelle** correspond à **X %** des situations passées ayant conduit à une
**reconnaissance Cat-Nat sécheresse** ici » — lecture **complémentaire** et **indicative**.
Atout : le **SWI CatNat est l'indice officiel** d'instruction sécheresse → forte cohérence
attendue entre nos `z_SWI` passés et les années de reconnaissance. **SWI seul** en v2.0 (IPS
reporté : couverture/historique trop partiels pour calibrer).

**Substrat** (`transform/h_calib.build_commune_swi_hist`) : `z_SWI` communal mensuel sur **tout
l'historique de calibration** (`SWI_CALIB_FROM` = 1990 →, premières reconnaissances en 1990),
distinct de la fenêtre **servie** (2017→) — réutilise `build_swi_anomalie` (plancher abaissé) +
`build_commune_swi` (mêmes poids maille↔commune, même climatologie ⇒ z_SWI cohérents).

**Définition** (`transform/h_calib.build_commune_h`, réf. Python `metric.severite`/`h_empirical_cdf`) :
1. **Sévérité** `s = −z_SWI` (sec ⇒ `s>0`).
2. **Sévérité-pic par évènement reconnu** : pour chaque (commune, arrêté GASPAR), `s_evt =
   max(−z_SWI)` sur la **fenêtre d'évènement** `[dat_deb, dat_fin]` **bornée** aux
   `H_EVENT_MAX_MONTHS` (= 24) derniers mois (les périodes GASPAR vont de 0 à ~160 mois — médiane
   5 ; le cap écarte les fenêtres aberrantes). Une « situation » = un évènement reconnu.
3. **Pool** des `{s_evt}` par **département** (`z_SWI` déjà standardisé par maille×mois ⇒ seuil de
   reconnaissance assez homogène ; le département capte l'hétérogénéité résiduelle), **repli
   national** si < `H_MIN_POOL_DEPT` (= 30) évènements (`h_pool_level` ∈ {departement, national}).
4. **`H = CDF empirique`** de la sévérité courante `s_now = −z_SWI` (mois servi) dans le pool :
   `h_proba = #{s_evt ≤ s_now}/#pool` ∈ [0,1], **monotone croissante** en sécheresse (donc en `T`).
   `h_n_events` = taille du pool. Sec extrême ⇒ `H→1` ; mois normal/humide ⇒ `H→0`.

**Lecture / gating** : `H` est **complémentaire** (n'entre **pas** dans `ip_rga_score`). Affiché
seulement si `E>0`/`has_rga_coverage` (pas de percentile sécheresse sur une commune non argileuse) ;
`E=0 ⇒ H` NULL. La commune montre aussi son histoire propre (`catnat_freq`, `dernier_arrete`,
`annees_reco`) à côté.

**Caveats (à afficher)** : (a) reconnaissance partiellement **administrative** → indicatif, *pas*
une probabilité de reconnaissance ; (b) GASPAR = **positifs seulement** (pas de négatifs) ⇒ `H` est
un **percentile de calibration** ; (c) **asymétrie** pic-de-fenêtre (`s_evt`) vs mois courant
(`s_now`) ⇒ `H` **conservateur** (un mois unique dépasse rarement le pic d'une sécheresse reconnue ;
`H` ne « monte » franchement que lors d'une sécheresse marquée — comportement voulu d'une boussole
complémentaire) ; (d) **non-stationnarité** climatique (comme SWI/IPS) ; (e) un évènement **sans
`z_SWI` mesurable** dans sa fenêtre (antérieur à 1990, trou de données) ne contribue pas au pool —
`h_n_events` et `H_MIN_POOL_DEPT` comptent donc les évènements **mesurables**. Pooling départemental
et `SWI_CALIB_FROM`/`H_EVENT_MAX_MONTHS`/`H_MIN_POOL_DEPT` **paramétrables**.

## Score final
- **MVP v0** : pas de dynamique. On affiche `E` et `J` (carte de l'enjeu + flag reclassement 2026).
- **v1 (la boussole)** :
```
ip_rga_score = clamp( round( 100 * E * T ** GAMMA ), 0, 100 )   # GAMMA = 0.8 (contraste)
ip_rga_niveau = bin(ip_rga_score) → {Très faible, Faible, Modérée, Élevée, Très élevée}
```
Justification : `E` **borne** le risque possible (pas d'argile ⇒ pas de RGA), `T` **module** selon la sécheresse du moment. `GAMMA=0.8` accentue un peu le contraste sec/humide (`T^0.8 > T` pour `T<1`).
- **Seuils des 5 niveaux** : **quantiles nationaux** du `ip_rga_score` calculés **sur les communes exposées (`E>0`), poolés sur toute la fenêtre servie** (et non par mois) → seuils **stables** ⇒ couleurs **comparables d'un mois à l'autre** (le curseur de date ne déplace pas l'échelle). Quintiles par défaut (20/40/60/80 %), stockés et exposés via `/meta` (`seuils_niveaux`), documentés. `E=0` ou hors couverture RGA ⇒ `ip_rga_niveau` NULL (affiché « Pas d'argile / hors couverture » via `has_rga_coverage`, jamais un faux « Très faible »).
- **v2** : ajoute `H` (**percentile empirique de calibration** vs les sécheresses reconnues, *pas*
  une probabilité de reconnaissance) en **lecture complémentaire** — `H` n'entre pas dans le score.

## Propriétés à tester (voir `CONCEPTION.md` §11)
- `ip_rga_score ∈ [0,100]` ; **monotonie** : à `E` fixe, plus sec (`T`↑) ⇒ score ≥ ; reproductible.
- `E = 0` ⇒ `ip_rga_score = 0`. Couverture : 100 % des communes ont un `T` (via SWI) même sans IPS.
- Cohérence temporelle : un mois documenté très sec doit ressortir au-dessus d'un mois humide.
- **`H` (v2)** : `h_proba ∈ [0,1]` ; **monotonie** (à commune fixe, plus sec ⇒ `H ≥`) ; parité
  SQL↔`metric.h_empirical_cdf` ; cohérence (un mois de sécheresse marquée — 2017, 2022 — ressort
  bien au-dessus d'un mois humide ; repli pool national vérifié).

## Caveats à afficher
Indice **territorial et indicatif** ; ne prédit pas de fissures par maison ; dépend de modèles (SIM/SWI, IPS) et d'une calibration corrélationnelle ; sources et dates affichées. **N'est pas** un conseil d'achat/assurance ni une expertise.
