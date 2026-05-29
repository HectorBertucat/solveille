"""Tests API FastAPI (fiche commune, meta, noindex, 404) sur un mart fixture."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from solveille.api import deps
from solveille.api.main import app
from solveille.common import duckdb_io

_MART = """SELECT * FROM (VALUES
    ('31555','Toulouse','31', 0.878, 21578.0, 7600000000.0, 3760.0, FALSE,
     '2026-05-28','2026-05-28','2026-05-28','2026-05-28','2026-05-28','2026-05-28'),
    ('75056','Paris','75', 0.0, NULL::DOUBLE, NULL::DOUBLE, NULL::DOUBLE, FALSE,
     '2026-05-28','2026-05-28','2026-05-28','2026-05-28','2026-05-28','2026-05-28')
  ) t(insee, nom, code_dept, E, n_maisons_exposees, valeur_bati_exposee_eur,
      prix_median_maison_eur_m2, basculement_2026,
      last_updated_admin_express, last_updated_rga, last_updated_bascule,
      last_updated_insee, last_updated_fideli, last_updated_dvf)"""


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    mart = tmp_path / "commune_pression.parquet"
    with duckdb_io.connection() as con:
        con.execute(f"COPY ({_MART}) TO '{mart}' (FORMAT PARQUET)")
    monkeypatch.setattr(deps, "mart_path", lambda: mart)
    return TestClient(app)


def test_commune_found_with_noindex(client: TestClient) -> None:
    r = client.get("/communes/31555")
    assert r.status_code == 200
    body = r.json()
    assert body["nom"] == "Toulouse"
    assert body["E"] == pytest.approx(0.878)
    assert body["valeur_bati_exposee_eur"] == pytest.approx(7.6e9)
    assert body["ip_rga_score"] is None  # pas de T en v0
    assert "noindex" in r.headers["x-robots-tag"]  # garde-fou DVF


def test_commune_not_found(client: TestClient) -> None:
    assert client.get("/communes/00000").status_code == 404


def test_meta(client: TestClient) -> None:
    r = client.get("/meta")
    assert r.status_code == 200
    body = r.json()
    assert body["n_communes"] == 2
    assert "disclaimer" in body
    assert body["last_updated_rga"] == "2026-05-28"


def test_robots_disallows(client: TestClient) -> None:
    r = client.get("/robots.txt")
    assert "Disallow: /" in r.text
