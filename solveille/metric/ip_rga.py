"""Indice IP-RGA — composantes (voir `docs/metric.md`). Fonctions pures, testables.

**v0** : on ne calcule que l'**exposition E** (statique) et l'**enjeu J** (valeur de bâti
exposé). La **tension hydrique T** (SWI/IPS) et le **score IP-RGA** = `100·E·T^γ` arrivent
en v1 — `ip_rga_score`/`niveau` restent donc NULL en v0.

Ces fonctions sont la **référence** (propriétés testées) ; le mart les réplique en SQL
vectorisé (`transform/mart.py`). Garder les deux en phase.
"""

from __future__ import annotations

#: Poids par défaut de E (documentés, ajustables) — surface d'aléa vs vulnérabilité bâti.
W_SURFACE = 0.6
W_BATI = 0.4


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
