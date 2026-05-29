"""Propriétés de la tension hydrique T et du score IP-RGA (docs/metric.md §11).

Monotonie (plus sec ⇒ score ≥), `E=0 ⇒ score=0`, bornes `[0,100]`, `T=dry_SWI` sans IPS,
binning des 5 niveaux.
"""

from __future__ import annotations

import pytest

from solveille.metric.ip_rga import (
    NIVEAU_LABELS,
    dry_intensity,
    ip_rga_score,
    niveau_index,
    niveau_label,
    sigma,
    tension_t,
)


def test_sigma_bounds_and_center() -> None:
    assert sigma(0.0) == pytest.approx(0.5)
    assert 0.0 < sigma(-10) < sigma(0) < sigma(10) < 1.0


def test_dry_intensity_decreasing_in_z() -> None:
    # sec (z<0) ⇒ dry élevé ; humide (z>0) ⇒ dry faible ; z=0 ⇒ 0.5 ; monotone décroissante.
    assert dry_intensity(0.0) == pytest.approx(0.5)
    vals = [dry_intensity(z) for z in (-2, -1, 0, 1, 2)]
    assert vals == sorted(vals, reverse=True)  # décroissante en z
    assert dry_intensity(None) is None
    assert 0.0 < dry_intensity(5) < dry_intensity(-5) < 1.0


def test_tension_t_swi_only_when_no_ips() -> None:
    d = dry_intensity(-1.0)
    assert tension_t(d) == d  # pas d'IPS ⇒ T = dry_SWI
    assert tension_t(d, None) == d
    assert tension_t(None, None) is None


def test_tension_t_weighted_when_ips_present() -> None:
    t = tension_t(0.8, 0.4, w_swi=1.0, w_ips=1.0)
    assert t == pytest.approx(0.6)  # moyenne des deux
    # w_ips=0 ⇒ ignore l'IPS (comportement v1.0)
    assert tension_t(0.8, 0.4, w_swi=1.0, w_ips=0.0) == pytest.approx(0.8)


def test_score_bounds_and_round() -> None:
    for e in (0.0, 0.3, 1.0):
        for t in (0.0, 0.2, 0.5, 1.0):
            s = ip_rga_score(e, t)
            assert s is None or (isinstance(s, int) and 0 <= s <= 100)
    assert ip_rga_score(1.0, 1.0) == 100
    assert ip_rga_score(None, 0.5) is None
    assert ip_rga_score(0.5, None) is None


def test_score_half_up_matches_duckdb() -> None:
    # 100·0.5·0.25 = 12.5 → half-up = 13 (et non 12 de l'arrondi bancaire) → parité avec DuckDB.
    assert ip_rga_score(0.5, 0.25, gamma=1.0) == 13
    assert ip_rga_score(0.5, 0.75, gamma=1.0) == 38  # 37.5 → 38


def test_score_zero_when_e_zero() -> None:
    # E=0 (pas d'argile) ⇒ score 0 quel que soit T (propriété forte).
    for t in (0.0, 0.5, 0.99, 1.0):
        assert ip_rga_score(0.0, t) == 0


def test_score_monotonic_in_t_at_fixed_e() -> None:
    e = 0.6
    scores = [ip_rga_score(e, t) for t in (0.1, 0.3, 0.5, 0.7, 0.9, 1.0)]
    assert scores == sorted(scores)  # plus sec (T↑) ⇒ score ≥ (monotone)
    assert scores[-1] >= scores[0]


def test_niveau_binning() -> None:
    seuils = [10.0, 20.0, 30.0, 40.0]  # 4 bornes → 5 niveaux
    assert niveau_index(5, seuils) == 0
    assert niveau_index(10, seuils) == 0  # borne incluse en bas
    assert niveau_index(15, seuils) == 1
    assert niveau_index(35, seuils) == 3
    assert niveau_index(50, seuils) == 4
    assert niveau_index(None, seuils) is None
    assert niveau_label(50, seuils) == NIVEAU_LABELS[4] == "Très élevée"
    assert niveau_label(None, seuils) is None
