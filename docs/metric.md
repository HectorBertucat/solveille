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
- **`z_SWI`** : anomalie standardisée du SWI CatNat de la maille (vs sa distribution **du même mois** sur l'historique). Sécheresse ⇒ `z_SWI` négatif.
- **`z_IPS`** : IPS du/des piézomètre(s) représentatif(s) de la commune (niveau courant rapporté à la distribution mensuelle historique). Sécheresse ⇒ `z_IPS` négatif.

On convertit en intensité de sécheresse `[0,1]` (1 = très sec) :
```
dry_SWI = sigma(-z_SWI)            # logistique, centrée
dry_IPS = sigma(-z_IPS)
T = w_swi * dry_SWI + w_ips * dry_IPS      # si IPS indisponible : T = dry_SWI
```
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
ip_rga_score = round( 100 * E * T_gamma )      # T_gamma = T ** gamma, gamma ∈ [0.7,1] pour le contraste
ip_rga_niveau = bin(ip_rga_score) → {Très faible, Faible, Modérée, Élevée, Très élevée}
```
Justification : `E` **borne** le risque possible (pas d'argile ⇒ pas de RGA), `T` **module** selon la sécheresse du moment. Seuils des 5 niveaux fixés sur la distribution nationale (quantiles) et documentés.
- **v2** : intègre `H` (probabilité empirique de reconnaissance) en lecture complémentaire.

## Propriétés à tester (voir `CONCEPTION.md` §11)
- `ip_rga_score ∈ [0,100]` ; **monotonie** : à `E` fixe, plus sec (`T`↑) ⇒ score ≥ ; reproductible.
- `E = 0` ⇒ `ip_rga_score = 0`. Couverture : 100 % des communes ont un `T` (via SWI) même sans IPS.
- Cohérence temporelle : un mois documenté très sec doit ressortir au-dessus d'un mois humide.

## Caveats à afficher
Indice **territorial et indicatif** ; ne prédit pas de fissures par maison ; dépend de modèles (SIM/SWI, IPS) et d'une calibration corrélationnelle ; sources et dates affichées. **N'est pas** un conseil d'achat/assurance ni une expertise.
