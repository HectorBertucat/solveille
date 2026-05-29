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
- **`z_IPS`** (v1.1) : IPS du/des piézomètre(s) représentatif(s) de la commune (niveau courant rapporté à la distribution mensuelle historique). Sécheresse ⇒ `z_IPS` négatif.

On convertit en intensité de sécheresse `[0,1]` (1 = très sec) via la **logistique** :
```
sigma(x) = 1 / (1 + exp(-x))
dry_SWI  = sigma(-GAIN * z_SWI)            # z_SWI<0 (sec) ⇒ dry_SWI→1 ; z=0 ⇒ 0.5
dry_IPS  = sigma(-GAIN * z_IPS)
T = w_swi * dry_SWI + w_ips * dry_IPS      # si IPS indisponible : T = dry_SWI
```
- **Constantes** (documentées, ajustables) : `GAIN = 1.0` (pente ; `z=−1⇒dry≈0.73`, `z=−2⇒0.88`), `w_swi`/`w_ips` (v1.1, `w_swi=1` tant que pas d'IPS).
- **Mois « normal » ⇒ `T = 0.5`** (z=0) : T module E sans l'annuler — la pression reste à mi-échelle hors anomalie. Un mois sec pousse T→1, un mois humide T→0.
- **Lissage** : le SWI CatNat est déjà une **moyenne glissante 3 mois** (le nowcast est donc lissé, pas instantané — à afficher).
- **Climatologie** : μ/σ calculés par (maille, mois calendaire) sur **tout l'historique disponible (1960→)** pour maximiser l'échantillon et la robustesse de σ. *Caveat assumé* : la tendance climatique rend les anomalies récentes légèrement plus « sèches » vs une normale longue — cohérent avec « pression vs normale historique ». Période de référence **paramétrable** (ex. normale 1991-2020) si besoin. σ≈0 (maille quasi constante un mois) ⇒ `z_SWI` NULL+flag.
- **SWI = signal universel** (grille 8 km, couverture totale, variable officielle Cat-Nat). **IPS = raffinement local** là où une station représentative existe → pondération `w_ips` réduite (ou nulle) sans station fiable. Exposer un niveau de **confiance** par commune (présence/qualité de l'IPS).

### J — Enjeu (statique, € et stock)
```
valeur_bati_exposee_eur ≈ n_maisons_exposees(commune) * surface_mediane * prix_median_maison(commune, DVF)
```
+ `n_tx_zone_exposee_12m` (transactions récentes de maisons en zone exposée → acheteurs potentiellement non avertis, d'autant que le zonage 2026 s'applique aux ventes depuis juillet 2026). `J` sert à **prioriser/illustrer**, pas à gonfler le score de pression.

### H — Calibration historique (v2)
À partir de GASPAR (arrêtés sécheresse de la commune) et des états SWI/IPS passés, estimer un **seuil empirique** : « les conditions actuelles correspondent à X % des situations ayant conduit à une reconnaissance ici ». **Indicatif** (la reconnaissance dépend aussi de critères administratifs).

## Score final
- **MVP v0** : pas de dynamique. On affiche `E` et `J` (carte de l'enjeu + flag reclassement 2026).
- **v1 (la boussole)** :
```
ip_rga_score = clamp( round( 100 * E * T ** GAMMA ), 0, 100 )   # GAMMA = 0.8 (contraste)
ip_rga_niveau = bin(ip_rga_score) → {Très faible, Faible, Modérée, Élevée, Très élevée}
```
Justification : `E` **borne** le risque possible (pas d'argile ⇒ pas de RGA), `T` **module** selon la sécheresse du moment. `GAMMA=0.8` accentue un peu le contraste sec/humide (`T^0.8 > T` pour `T<1`).
- **Seuils des 5 niveaux** : **quantiles nationaux** du `ip_rga_score` calculés **sur les communes exposées (`E>0`), poolés sur toute la fenêtre servie** (et non par mois) → seuils **stables** ⇒ couleurs **comparables d'un mois à l'autre** (le curseur de date ne déplace pas l'échelle). Quintiles par défaut (20/40/60/80 %), stockés et exposés via `/meta` (`seuils_niveaux`), documentés. `E=0` ou hors couverture RGA ⇒ `ip_rga_niveau` NULL (affiché « Pas d'argile / hors couverture » via `has_rga_coverage`, jamais un faux « Très faible »).
- **v2** : intègre `H` (probabilité empirique de reconnaissance) en lecture complémentaire.

## Propriétés à tester (voir `CONCEPTION.md` §11)
- `ip_rga_score ∈ [0,100]` ; **monotonie** : à `E` fixe, plus sec (`T`↑) ⇒ score ≥ ; reproductible.
- `E = 0` ⇒ `ip_rga_score = 0`. Couverture : 100 % des communes ont un `T` (via SWI) même sans IPS.
- Cohérence temporelle : un mois documenté très sec doit ressortir au-dessus d'un mois humide.

## Caveats à afficher
Indice **territorial et indicatif** ; ne prédit pas de fissures par maison ; dépend de modèles (SIM/SWI, IPS) et d'une calibration corrélationnelle ; sources et dates affichées. **N'est pas** un conseil d'achat/assurance ni une expertise.
