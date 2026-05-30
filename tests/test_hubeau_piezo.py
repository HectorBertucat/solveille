"""Connecteur Hub'eau piézo : bornage de la profondeur d'historique téléchargée (volume)."""

from __future__ import annotations

from datetime import date

from solveille.ingest.hubeau_piezo import MAX_HISTORY_YEARS, _history_start


def test_history_start_caps_very_long_records() -> None:
    # Station 1899→2026 : on ne télécharge que ~35 dernières années (≥ 30 ans pour la climato BRGM).
    d_fin = date(2026, 5, 1)
    start = _history_start(date(1899, 3, 1), d_fin)
    assert (d_fin - start).days == round(MAX_HISTORY_YEARS * 365.25)  # exactement la borne 35 ans
    assert date(1991, 1, 1) < start < date(1991, 12, 31)  # ~2026 − 35


def test_history_start_keeps_short_records() -> None:
    # Station plus courte que la borne : début inchangé (toute la chronique).
    assert _history_start(date(2010, 1, 1), date(2024, 1, 1)) == date(2010, 1, 1)


def test_history_start_idempotent() -> None:
    args = (date(1950, 6, 1), date(2026, 5, 1))
    assert _history_start(*args) == _history_start(*args)  # ancré sur date_fin, pas sur "today"
