"""Propriétés de l'IPS piézométrique (docs/metric.md §T, ADR-018).

NQT centrée-réduite (N(0,1) par construction), classes BRGM aux seuils, monotonie (sec = bas),
et confiance bornée pilotant `w_ips`.
"""

from __future__ import annotations

import math
import statistics

import pytest

from solveille.metric.ip_rga import (
    IPS_CLASS_LABELS,
    IPS_CLASS_SEUILS,
    IPS_CONF_FLOOR,
    IPS_FULL_YEARS,
    IPS_MIN_YEARS,
    W_IPS_MAX,
    confiance_ips,
    dry_intensity,
    ips_classe,
    ips_nqt,
    probit,
    tension_t,
)


def test_probit_median_and_symmetry() -> None:
    assert probit(0.5) == pytest.approx(0.0, abs=1e-12)
    assert probit(0.975) == pytest.approx(1.959963985, abs=1e-6)  # ~+1.96
    assert probit(0.025) == pytest.approx(-1.959963985, abs=1e-6)
    # impair : Φ⁻¹(1-p) = -Φ⁻¹(p)
    for p in (0.01, 0.1, 0.3, 0.4):
        assert probit(1 - p) == pytest.approx(-probit(p), abs=1e-9)
    # clamp anti-infini aux extrêmes (pas de ValueError / inf)
    assert math.isfinite(probit(0.0))
    assert math.isfinite(probit(1.0))
    assert probit(0.0) < probit(1.0)


def test_ips_nqt_is_centered_reduced_on_large_sample() -> None:
    # Sur un grand échantillon, repositionner chaque valeur par NQT ⇒ distribution ≈ N(0,1)
    # quelle que soit la forme d'origine (ici une loi asymétrique log-normale-like).
    hist = [math.exp(0.4 * x) for x in range(-60, 60)]  # asymétrique, bornée à gauche
    nqts = [ips_nqt(v, hist) for v in hist]
    vals = [z for z in nqts if z is not None]
    assert statistics.fmean(vals) == pytest.approx(0.0, abs=0.05)
    assert statistics.pstdev(vals) == pytest.approx(1.0, abs=0.1)
    # monotone croissante en value (sec = niveau bas = NQT bas)
    assert vals == sorted(vals)


def test_ips_nqt_weibull_positions() -> None:
    hist = [10.0, 20.0, 30.0, 40.0]  # n=4
    # valeur min : r = 1 + #{h<5} = 1 ⇒ p = 1/5 = 0.2
    assert ips_nqt(5.0, hist) == pytest.approx(probit(0.2), abs=1e-12)
    # valeur max : r = 1 + #{h<50} = 5 ⇒ p = 5/5... = 5/(4+1)=1.0 → clampé < +inf, fini
    assert math.isfinite(ips_nqt(50.0, hist) or float("nan"))
    # médiane d'un échantillon : ~0
    assert ips_nqt(25.0, [10, 20, 30, 40, 50]) == pytest.approx(probit(3 / 6), abs=1e-12)
    assert ips_nqt(None, hist) is None
    assert ips_nqt(5.0, []) is None
    assert ips_nqt(5.0, [None, None]) is None


def test_ips_classe_thresholds() -> None:
    assert len(IPS_CLASS_SEUILS) == 6 and len(IPS_CLASS_LABELS) == 7
    # juste sous/au-dessus de chaque borne
    assert ips_classe(-2.0) == 0  # Très bas (sec)
    assert ips_classe(-1.282) == 0  # borne incluse en bas
    assert ips_classe(-1.0) == 1  # Bas
    assert ips_classe(0.0) == 3  # Autour de la moyenne
    assert ips_classe(2.0) == 6  # Très haut (humide)
    assert ips_classe(None) is None
    # cohérence : les seuils sont les quantiles N(0,1) des percentiles 10/20/40/60/80/90
    for p, s in zip((0.1, 0.2, 0.4, 0.6, 0.8, 0.9), IPS_CLASS_SEUILS, strict=True):
        assert probit(p) == pytest.approx(s, abs=2e-3)


def test_confiance_ips_bounds_and_history_ramp() -> None:
    assert confiance_ips(None) == 0.0
    assert confiance_ips(10) == 0.0  # < 15 ans ⇒ pas d'IPS
    assert confiance_ips(IPS_MIN_YEARS) == pytest.approx(IPS_CONF_FLOOR)  # plancher à 15 ans
    assert confiance_ips(IPS_FULL_YEARS) == pytest.approx(1.0)  # plein à 30 ans
    assert confiance_ips(60) == pytest.approx(1.0)  # plafonné
    # monotone croissante entre 15 et 30 ans
    ramp = [confiance_ips(y) for y in range(IPS_MIN_YEARS, IPS_FULL_YEARS + 1)]
    assert ramp == sorted(ramp)
    # facteurs nappe/représentativité multiplient et restent dans [0,1]
    assert confiance_ips(30, f_nappe=0.5) == pytest.approx(0.5)
    assert 0.0 <= confiance_ips(30, f_nappe=0.5, f_repr=0.3) <= 1.0


def test_t_combination_with_w_ips_from_confidence() -> None:
    """Contrat de la combinaison T (branchée dans le mart en M2) : w_ips = confiance·W_IPS_MAX."""
    dry_swi, dry_ips = dry_intensity(-1.0), dry_intensity(-0.2)
    # Pas de station (confiance 0 ⇒ w_ips 0) ⇒ T = dry_SWI (repli universel).
    w0 = confiance_ips(10) * W_IPS_MAX
    assert tension_t(dry_swi, dry_ips, w_ips=w0) == pytest.approx(dry_swi)
    # Station fiable (30 ans) ⇒ w_ips = W_IPS_MAX ; T entre les deux signaux, borné (0,1).
    w_full = confiance_ips(30) * W_IPS_MAX
    t = tension_t(dry_swi, dry_ips, w_ips=w_full)
    assert w_full == pytest.approx(0.5)
    assert min(dry_swi, dry_ips) < t < max(dry_swi, dry_ips)
    assert t == pytest.approx((1.0 * dry_swi + 0.5 * dry_ips) / 1.5)
    # Mois normal (z=0 partout ⇒ dry=0.5) ⇒ T=0.5 quel que soit w_ips (T module sans annuler E).
    for w in (0.0, 0.2, W_IPS_MAX):
        assert tension_t(0.5, 0.5, w_ips=w) == pytest.approx(0.5)
    # Monotonie : à dry_SWI fixe, plus sec côté IPS (dry_ips↑) ⇒ T ≥.
    ts = [tension_t(dry_swi, d, w_ips=w_full) for d in (0.1, 0.4, 0.6, 0.9)]
    assert ts == sorted(ts) and all(0.0 < x < 1.0 for x in ts)
