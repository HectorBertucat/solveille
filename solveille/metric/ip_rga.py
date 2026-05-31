"""Indice IP-RGA — composantes (voir `docs/metric.md`). Fonctions pures, testables.

- **E** (exposition argile, statique) et **J** (valeur de bâti exposé) : v0.
- **T** (tension hydrique, dynamique) et **score IP-RGA** = `round(100·E·T^γ)` sur 5 niveaux : v1.
  En v1.0, `T = dry_SWI` (SWI seul) ; l'IPS piézométrique (`w_ips`) arrive en v1.1.

Ces fonctions sont la **référence** (propriétés testées) ; le mart les réplique en SQL
vectorisé (`transform/mart.py`). Garder les deux en phase.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence

#: Poids par défaut de E (documentés, ajustables) — surface d'aléa vs vulnérabilité bâti.
W_SURFACE = 0.6
W_BATI = 0.4

#: Tension hydrique / score (cf. `docs/metric.md`). GAMMA = contraste du score (T^GAMMA) ;
#: GAIN = pente de la logistique ; w_swi/w_ips = pondération SWI/IPS (v1.0 : SWI seul).
GAMMA = 0.8
GAIN = 1.0
W_SWI = 1.0
W_IPS = 0.0

#: Poids **maximal** de l'IPS dans T (à confiance = 1). Le SWI reste dominant (`w_swi = 1`) :
#: à confiance max l'IPS pèse la moitié du SWI. `w_ips = confiance · W_IPS_MAX` (v1.1, ADR-018).
W_IPS_MAX = 0.5

#: Libellés des 5 niveaux de pression (index 0→4, code 1→5).
NIVEAU_LABELS = ("Très faible", "Faible", "Modérée", "Élevée", "Très élevée")

#: Seuils standard-normaux des **7 classes BRGM** de l'IPS (Indicateur Piézométrique Standardisé,
#: méthode Seguin 2014 / BRGM RP-67249) : ce sont les quantiles N(0,1) des probabilités
#: {0.1, 0.2, 0.4, 0.6, 0.8, 0.9}. Bornes croissantes → 7 classes (index 0→6). L'IPS est en
#: unités N(0,1) : **IPS haut = nappe haute = humide** ; IPS bas = sec (cohérent avec `z_SWI`,
#: `dry = sigma(-GAIN·z)`).
IPS_CLASS_SEUILS: tuple[float, ...] = (-1.282, -0.842, -0.253, 0.253, 0.842, 1.282)
IPS_CLASS_LABELS: tuple[str, ...] = (
    "Très bas",
    "Bas",
    "Modérément bas",
    "Autour de la moyenne",
    "Modérément haut",
    "Haut",
    "Très haut",
)

#: Bornes d'historique (années) pour la confiance IPS : `< IPS_MIN_YEARS` ⇒ IPS non calculé
#: (méthode BRGM : ≥ 15 ans requis, ≥ 30 idéal) ; la part « historique » de la confiance monte
#: de `IPS_CONF_FLOOR` (à MIN) à 1.0 (à `IPS_FULL_YEARS`).
IPS_MIN_YEARS = 15
IPS_FULL_YEARS = 30
IPS_CONF_FLOOR = 0.4

_STD_NORMAL = statistics.NormalDist()
#: Borne anti-infini de Φ⁻¹ aux extrêmes (la position de Weibull `rank/(n+1)` reste dans (0,1),
#: mais on clampe par sécurité — comportement répliqué tel quel par l'UDF DuckDB `probit`).
_PROBIT_EPS = 1e-9


def clamp01(x: float) -> float:
    """Borne dans [0, 1]."""
    return max(0.0, min(1.0, x))


def exposition_e(
    part_alea_moyen_fort: float | None,
    part_maisons_vulnerables: float | None,
    *,
    w_surface: float = W_SURFACE,
    w_bati: float = W_BATI,
) -> float:
    """Exposition argile E ∈ [0, 1] : aléa moyen+fort pondéré par la vulnérabilité du bâti.

    `E = clamp01(w_surface·part_alea_moyen_fort + w_bati·part_maisons_vulnerables)`.
    Si la vulnérabilité est inconnue (EPCI Fideli non apparié), on retombe sur l'exposition
    surfacique seule (`E = clamp01(part_alea_moyen_fort)`) plutôt que de la sous-pondérer.

    **Pas d'argile ⇒ E = 0** : si `part_alea_moyen_fort = 0`, E = 0 quelle que soit la
    vulnérabilité — `part_maisons_vulnerables` étant la part *des maisons exposées*, elle ne
    peut pas créer d'exposition là où il n'y en a pas (sinon la vulnérabilité EPCI fuirait
    sur des communes non exposées, ex. Paris). Garantit la propriété `E=0 ⇒ score=0`.
    """
    pmf = part_alea_moyen_fort or 0.0
    if pmf <= 0.0:
        return 0.0
    if part_maisons_vulnerables is None:
        return clamp01(pmf)
    return clamp01(w_surface * pmf + w_bati * part_maisons_vulnerables)


def valeur_bati_exposee(
    n_maisons_exposees: float | None,
    surface_mediane_maison: float | None,
    prix_median_eur_m2: float | None,
) -> float | None:
    """Enjeu J : ordre de grandeur de la valeur de bâti exposé (€).

    `≈ n_maisons_exposees · surface_moyenne · prix_médian(€/m²)`. NULL si une entrée manque
    (commune sans EPCI apparié ou sans transaction DVF). Sert à **prioriser/illustrer**,
    n'entre pas dans le score de pression.
    """
    if n_maisons_exposees is None or surface_mediane_maison is None or prix_median_eur_m2 is None:
        return None
    return n_maisons_exposees * surface_mediane_maison * prix_median_eur_m2


def sigma(x: float) -> float:
    """Logistique standard `1/(1+e^{-x})` ∈ (0, 1)."""
    return 1.0 / (1.0 + math.exp(-x))


def dry_intensity(z: float | None, *, gain: float = GAIN) -> float | None:
    """Intensité de sécheresse ∈ (0, 1) à partir d'une anomalie standardisée `z`.

    `dry = sigma(-gain·z)` : sec (`z<0`) ⇒ `dry→1` ; humide (`z>0`) ⇒ `dry→0` ; `z=0 ⇒ 0.5`.
    `z` NULL ⇒ None (signal indisponible).
    """
    if z is None:
        return None
    return sigma(-gain * z)


def tension_t(
    dry_swi: float | None,
    dry_ips: float | None = None,
    *,
    w_swi: float = W_SWI,
    w_ips: float = W_IPS,
) -> float | None:
    """Tension hydrique T ∈ (0, 1) : combinaison pondérée des intensités de sécheresse.

    `T = (w_swi·dry_swi + w_ips·dry_ips)/(w_swi+w_ips)`. **Si l'IPS est indisponible
    (`dry_ips=None`), `T = dry_swi`** (cas v1.0). Si seul l'IPS est dispo, `T = dry_ips`.
    """
    if dry_swi is None:
        return dry_ips
    if dry_ips is None or w_ips <= 0.0:
        return dry_swi
    return (w_swi * dry_swi + w_ips * dry_ips) / (w_swi + w_ips)


def probit(p: float) -> float:
    """Quantile normal standard Φ⁻¹(p), p ∈ (0,1) ; médiane `p=0.5 → 0`, clampé loin de 0/1.

    **Source unique de vérité de la NQT** : enregistré comme UDF DuckDB dans
    `transform/piezo_ips` (même fonction côté SQL) → parité Python↔SQL exacte, pas de
    ré-implémentation d'une approximation rationnelle.
    """
    q = min(max(p, _PROBIT_EPS), 1.0 - _PROBIT_EPS)
    return _STD_NORMAL.inv_cdf(q)


def ips_nqt(value: float | None, historical: Sequence[float | None]) -> float | None:
    """IPS par **transformée quantile-normale** (NQT, méthode BRGM).

    `value` (niveau NGF du mois) positionné dans `historical` (les niveaux du **même mois
    calendaire** sur l'historique de la station) via la **position de Weibull** :
    `r = 1 + #{h < value}`, `p = r/(n+1)` ∈ (0,1), puis `Φ⁻¹(p)`. Sortie N(0,1) **par
    construction** ⇒ `ips_classe` tombe exactement sur les percentiles. None si échantillon
    vide ou `value` None. Monotone croissante en `value` (même sens que le z plain : sec = bas).
    """
    if value is None:
        return None
    sample = [float(h) for h in historical if h is not None]
    n = len(sample)
    if n == 0:
        return None
    r = 1 + sum(1 for h in sample if h < value)
    return probit(r / (n + 1.0))


def ips_classe(nqt: float | None, seuils: Sequence[float] = IPS_CLASS_SEUILS) -> int | None:
    """Index de **classe BRGM** (0→6) d'un IPS NQT selon `seuils` (7 classes), ou None.

    `nqt ≤ seuils[0] → 0` (Très bas / sec) … `nqt > seuils[-1] → 6` (Très haut / humide).
    Même binning que `niveau_index` mais sur les 6 bornes des 7 classes piézométriques.
    """
    if nqt is None:
        return None
    for i, s in enumerate(seuils):
        if nqt <= s:
            return i
    return len(seuils)


def confiance_ips(
    n_years: float | None,
    *,
    f_nappe: float = 1.0,
    f_repr: float = 1.0,
) -> float:
    """Niveau de confiance de l'IPS d'une station ∈ [0, 1] (module `w_ips = confiance·W_IPS_MAX`).

    `clamp01(f_hist · f_nappe · f_repr)` :
    - `f_hist` : 0 si `n_years < IPS_MIN_YEARS` (IPS non fiable), sinon monte de `IPS_CONF_FLOOR`
      (à MIN) à 1.0 (à `IPS_FULL_YEARS`) — un historique long fiabilise la climatologie.
    - `f_nappe` : nappe **libre** = 1 (réagit à la sécheresse des sols), **captive** = 0.5,
      inconnue = 0.7 (BDLISA, M2 ; défaut 1.0 en M1).
    - `f_repr` : représentativité spatiale — 1 pour la commune-hôte (M1) ; décroît avec la
      distance pour le rattachement par rayon (M2).

    `confiance = 0` ⇒ `w_ips = 0` ⇒ **T = dry_SWI** (repli SWI universel garanti).
    """
    if n_years is None or n_years < IPS_MIN_YEARS:
        return 0.0
    span = IPS_FULL_YEARS - IPS_MIN_YEARS
    f_hist = (
        1.0
        if n_years >= IPS_FULL_YEARS
        else IPS_CONF_FLOOR + (1.0 - IPS_CONF_FLOOR) * (n_years - IPS_MIN_YEARS) / span
    )
    return clamp01(f_hist * f_nappe * f_repr)


#: Taille minimale du pool **départemental** de sévérités d'évènements reconnus pour calibrer
#: `H` localement ; en deçà, repli sur le pool **national** (cf. `h_empirical_cdf`, ADR-019).
H_MIN_POOL_DEPT = 30

#: Longueur **maximale** (mois) de la fenêtre d'un évènement reconnu pour la recherche du pic de
#: sévérité. Les périodes GASPAR `[dat_deb, dat_fin]` sont très hétérogènes (médiane 5 mois mais
#: **jusqu'à ~160 mois**) ; un `max(−z_SWI)` sur une fenêtre de 13 ans capterait un outlier sans
#: rapport avec la reconnaissance. On borne donc le pic aux `H_EVENT_MAX_MONTHS` mois finissant à
#: `dat_fin` (couvre une sécheresse pluri-saisonnière type 2022-2023, hors fenêtres aberrantes).
H_EVENT_MAX_MONTHS = 24


def severite(z: float | None) -> float | None:
    """Sévérité de sécheresse `s = -z` (anomalie standardisée) : sec (`z<0`) ⇒ `s>0`, humide
    ⇒ `s<0`. None ⇒ None. Convention partagée SWI/`H` (monotone **décroissante** en `z`)."""
    return None if z is None else -z


def h_empirical_cdf(s_now: float | None, pool: Sequence[float | None]) -> float | None:
    """Calibration historique `H` ∈ [0, 1] : **CDF empirique** de la sévérité courante `s_now`
    dans le `pool` des **sévérités-pics** des évènements ayant conduit à une reconnaissance.

    `H = #{s ∈ pool : s ≤ s_now} / #pool` — part des situations reconnues **au plus aussi
    sévères** qu'aujourd'hui. Sec extrême ⇒ `H→1` ; conditions normales/humides ⇒ `H→0`.
    **Monotone croissante en `s_now`** (donc en sécheresse, donc en `T`). None si `s_now` None
    ou `pool` vide. **Indicatif** : « la sécheresse actuelle ≥ X % des situations reconnues
    ici » — *pas* une probabilité de reconnaissance (GASPAR ne liste que des positifs).
    Référence Python du calcul SQL (`transform/h_calib`) — parité testée.
    """
    if s_now is None:
        return None
    sample = [float(s) for s in pool if s is not None]
    if not sample:
        return None
    return sum(1 for s in sample if s <= s_now) / len(sample)


def ip_rga_score(e: float | None, t: float | None, *, gamma: float = GAMMA) -> int | None:
    """Score IP-RGA ∈ [0, 100] : `round(100·E·T^γ)`. None si E ou T manquant.

    `E` borne le risque (pas d'argile ⇒ score 0), `T` module selon la sécheresse. `E=0 ⇒ 0`.
    """
    if e is None or t is None:
        return None
    bounded = min(100.0, max(0.0, 100.0 * e * (max(t, 0.0) ** gamma)))
    # arrondi *half-up* (et non bancaire) pour coller à `round()` de DuckDB → parité SQL stricte.
    return int(math.floor(bounded + 0.5))


def niveau_index(score: int | None, seuils: list[float]) -> int | None:
    """Index de niveau (0→4) d'un score, selon `seuils` (bornes croissantes, ex. quintiles).

    `score ≤ seuils[0] → 0` … `score > seuils[-1] → len(seuils)`. None ⇒ None (E=0 / hors
    couverture : pas de niveau, géré en amont — un « 0 pas d'argile » n'est pas « Très faible »).
    """
    if score is None:
        return None
    for i, s in enumerate(seuils):
        if score <= s:
            return i
    return len(seuils)


def niveau_label(score: int | None, seuils: list[float]) -> str | None:
    """Libellé du niveau (`NIVEAU_LABELS`) d'un score, ou None."""
    idx = niveau_index(score, seuils)
    return None if idx is None else NIVEAU_LABELS[idx]
