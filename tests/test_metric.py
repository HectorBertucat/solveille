"""Propriétés de l'indice (docs/metric.md §11) — fonctions pures E et J."""

from __future__ import annotations

import pytest

from solveille.metric.ip_rga import clamp01, exposition_e, valeur_bati_exposee


def test_e_in_bounds() -> None:
    for pmf in (0.0, 0.3, 0.994, 1.0):
        for vuln in (None, 0.0, 0.5, 1.0):
            e = exposition_e(pmf, vuln)
            assert 0.0 <= e <= 1.0


def test_e_zero_when_no_clay() -> None:
    """Pas d'aléa moyen+fort ⇒ E=0, même avec une vulnérabilité élevée (cas Paris)."""
    assert exposition_e(0.0, 0.9) == 0.0
    assert exposition_e(0.0, None) == 0.0
    assert exposition_e(None, 0.5) == 0.0


def test_e_formula_default_weights() -> None:
    assert exposition_e(0.994, 0.705) == pytest.approx(0.6 * 0.994 + 0.4 * 0.705)


def test_e_vuln_unknown_falls_back_to_surface() -> None:
    assert exposition_e(0.5, None) == pytest.approx(0.5)


def test_e_monotonic_in_exposure() -> None:
    # à vulnérabilité fixe, E croît avec la part d'aléa
    assert exposition_e(0.2, 0.5) < exposition_e(0.5, 0.5) < exposition_e(0.9, 0.5)


def test_e_clamped_to_one() -> None:
    assert exposition_e(1.0, 1.0) == pytest.approx(1.0)  # 0.6 + 0.4
    assert clamp01(1.5) == 1.0


def test_valeur_bati_exposee() -> None:
    assert valeur_bati_exposee(100, 90, 2000) == pytest.approx(100 * 90 * 2000)
    assert valeur_bati_exposee(None, 90, 2000) is None
    assert valeur_bati_exposee(100, None, 2000) is None
    assert valeur_bati_exposee(100, 90, None) is None
