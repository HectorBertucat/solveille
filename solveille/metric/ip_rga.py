"""Indice IP-RGA — composantes (voir `docs/metric.md`). Fonctions pures, testables.

- **E** (exposition argile, statique) et **J** (valeur de bâti exposé) : v0.
- **T** (tension hydrique, dynamique) et **score IP-RGA** = `round(100·E·T^γ)` sur 5 niveaux : v1.
  En v1.0, `T = dry_SWI` (SWI seul) ; l'IPS piézométrique (`w_ips`) arrive en v1.1.

Ces fonctions sont la **référence** (propriétés testées) ; le mart les réplique en SQL
vectorisé (`transform/mart.py`). Garder les deux en phase.
"""

from __future__ import annotations

import math

#: Poids par défaut de E (documentés, ajustables) — surface d'aléa vs vulnérabilité bâti.
W_SURFACE = 0.6
W_BATI = 0.4

#: Tension hydrique / score (cf. `docs/metric.md`). GAMMA = contraste du score (T^GAMMA) ;
#: GAIN = pente de la logistique ; w_swi/w_ips = pondération SWI/IPS (v1.0 : SWI seul).
GAMMA = 0.8
GAIN = 1.0
W_SWI = 1.0
W_IPS = 0.0

#: Libellés des 5 niveaux de pression (index 0→4, code 1→5).
NIVEAU_LABELS = ("Très faible", "Faible", "Modérée", "Élevée", "Très élevée")


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
