"""Tests unitaires du connecteur ADMIN EXPRESS (logique pure, offline)."""

from __future__ import annotations

from solveille.ingest.admin_express import _parse_title


def test_parse_title_gpkg() -> None:
    assert _parse_title("ADMIN-EXPRESS-COG-CARTO_4-0__GPKG_LAMB93_FXX_2026-01-01") == (
        "4-0",
        "GPKG",
        "LAMB93",
        "FXX",
        "2026-01-01",
    )


def test_parse_title_shp_branch() -> None:
    assert _parse_title("ADMIN-EXPRESS-COG-CARTO_3-1__SHP_LAMB93_FXX_2022-04-15") == (
        "3-1",
        "SHP",
        "LAMB93",
        "FXX",
        "2022-04-15",
    )


def test_parse_title_rejects_garbage() -> None:
    assert _parse_title("not-a-resource") is None
    # moins de 4 segments après le double underscore
    assert _parse_title("ADMIN-EXPRESS-COG-CARTO_4-0__GPKG_LAMB93") is None
