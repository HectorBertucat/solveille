"""Tests API FastAPI : fiche commune (mois servi), série, meta, noindex, 404, sur fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from solveille.api import deps
from solveille.api.main import app
from solveille.common import duckdb_io

# Mart statique : Toulouse (exposée, dernier mois 2025-12) + Paris (E=0, hors couverture).
# 8 dates last_updated_* : admin_express, rga, bascule, insee, fideli, dvf, swi, ips.
_LU = (
    "'2026-05-28','2026-05-28','2026-05-28','2026-05-28','2026-05-28','2026-05-28',"
    "'2026-05-29','2026-05-30'"
)  # …_swi (mensuel), _ips (IPS quotidien, le + récent)
_MART = f"""SELECT * FROM (VALUES
    ('31555','Toulouse','31','2025-12-01', 0.878, 21578.0, 7600000000.0, 3760.0, FALSE,
     45, 'Élevée', {_LU}),
    ('75056','Paris','75','2025-12-01', 0.0, NULL::DOUBLE, NULL::DOUBLE, NULL::DOUBLE, FALSE,
     0, NULL::VARCHAR, {_LU})
  ) t(insee, nom, code_dept, date, E, n_maisons_exposees, valeur_bati_exposee_eur,
      prix_median_maison_eur_m2, basculement_2026, ip_rga_score, ip_rga_niveau,
      last_updated_admin_express, last_updated_rga, last_updated_bascule,
      last_updated_insee, last_updated_fideli, last_updated_dvf, last_updated_swi,
      last_updated_ips)"""

# Mensuel : Toulouse sur 2 mois (sec en déc, plus humide en nov ; IPS local présent, classe
# BRGM) ; Paris gaté (E=0, pas d'IPS). Colonnes z_ips/dry_ips/ips_classe/confiance_t (v1.1).
_MENSUEL = """SELECT * FROM (VALUES
    ('31555', DATE '2025-11-01', -0.5, 0.62, -0.4, 0.60, 1, 0.62, 0.13, 30, 'Modérée', 3),
    ('31555', DATE '2025-12-01', -1.2, 0.77, -0.9, 0.71, 0, 0.77, 0.13, 45, 'Élevée', 4),
    ('75056', DATE '2025-11-01', -0.5, 0.62, NULL::DOUBLE, NULL::DOUBLE, NULL::INTEGER,
     0.62, 0.0, 0, NULL::VARCHAR, NULL::INTEGER),
    ('75056', DATE '2025-12-01', -1.2, 0.77, NULL::DOUBLE, NULL::DOUBLE, NULL::INTEGER,
     0.77, 0.0, 0, NULL::VARCHAR, NULL::INTEGER)
  ) t(insee, date_mois, z_swi, dry_swi, z_ips, dry_ips, ips_classe, T, confiance_t,
      ip_rga_score, ip_rga_niveau, ip_rga_niveau_code)"""


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    mart = tmp_path / "commune_pression.parquet"
    mensuel = tmp_path / "commune_pression_mensuel.parquet"
    seuils = tmp_path / "seuils_niveaux.json"
    with duckdb_io.connection() as con:
        con.execute(f"COPY ({_MART}) TO '{mart}' (FORMAT PARQUET)")
        con.execute(f"COPY ({_MENSUEL}) TO '{mensuel}' (FORMAT PARQUET)")
    seuils.write_text(json.dumps({"seuils": [24, 35, 47, 61], "labels": ["a"]}), encoding="utf-8")
    monkeypatch.setattr(deps, "mart_path", lambda: mart)
    monkeypatch.setattr(deps, "mensuel_path", lambda: mensuel)
    monkeypatch.setattr(deps, "seuils_path", lambda: seuils)
    return TestClient(app)


def test_commune_found_latest_month(client: TestClient) -> None:
    r = client.get("/communes/31555")
    assert r.status_code == 200
    body = r.json()
    assert body["nom"] == "Toulouse"
    assert body["E"] == pytest.approx(0.878)
    assert body["valeur_bati_exposee_eur"] == pytest.approx(7.6e9)
    # dernier mois fusionné depuis le mensuel
    assert body["date"] == "2025-12-01"
    assert body["ip_rga_score"] == 45 and body["ip_rga_niveau"] == "Élevée"
    assert body["T"] == pytest.approx(0.77)
    # IPS local (v1.1) exposé dans la fiche : classe BRGM + confiance
    assert body["ips_classe"] == 0 and body["z_ips"] == pytest.approx(-0.9)
    assert body["confiance_t"] == pytest.approx(0.13)
    assert body["last_updated_ips"] == "2026-05-30"
    assert "noindex" in r.headers["x-robots-tag"]  # garde-fou DVF


def test_commune_specific_month(client: TestClient) -> None:
    r = client.get("/communes/31555", params={"mois": "2025-11"})
    assert r.status_code == 200
    body = r.json()
    assert body["date"] == "2025-11-01"
    assert body["ip_rga_score"] == 30 and body["ip_rga_niveau"] == "Modérée"


def test_invalid_month_rejected(client: TestClient) -> None:
    assert client.get("/communes/31555", params={"mois": "2025/11"}).status_code == 422


def test_paris_gated(client: TestClient) -> None:
    body = client.get("/communes/75056").json()
    assert body["E"] == 0.0
    assert body["ip_rga_score"] == 0 and body["ip_rga_niveau"] is None  # E=0 ⇒ pas de niveau


def test_serie(client: TestClient) -> None:
    r = client.get("/communes/31555/serie")
    assert r.status_code == 200
    serie = r.json()["serie"]
    assert [p["date_mois"] for p in serie] == ["2025-11-01", "2025-12-01"]  # ordonné
    assert [p["ip_rga_score"] for p in serie] == [30, 45]


def test_commune_not_found(client: TestClient) -> None:
    assert client.get("/communes/00000").status_code == 404
    assert client.get("/communes/00000/serie").status_code == 404


def test_meta(client: TestClient) -> None:
    r = client.get("/meta")
    assert r.status_code == 200
    body = r.json()
    assert body["n_communes"] == 2
    assert "disclaimer" in body
    assert body["last_updated_rga"] == "2026-05-28"
    assert body["last_updated_swi"] == "2026-05-29"
    assert body["last_updated_ips"] == "2026-05-30"  # IPS nappes (rafraîchi quotidiennement)
    assert body["mois_disponibles"] == {"min": "2025-11-01", "max": "2025-12-01", "n": 2}
    assert body["seuils_niveaux"]["seuils"] == [24, 35, 47, 61]


def test_robots_disallows(client: TestClient) -> None:
    r = client.get("/robots.txt")
    assert "Disallow: /" in r.text


# Mart « ancien schéma » (code v1.1 déployé AVANT le rebuild du mart) : ni last_updated_ips
# ni ips_classe. L'API doit rester robuste (pas de 500), champs IPS absents → None.
_LU7 = "'2026-05-28','2026-05-28','2026-05-28','2026-05-28','2026-05-28','2026-05-28','2026-05-29'"
_MART_OLD = f"""SELECT * FROM (VALUES
    ('31555','Toulouse','31','2025-12-01', 0.878, 21578.0, 7.6e9, 3760.0, FALSE,
     45, 'Élevée', {_LU7})
  ) t(insee, nom, code_dept, date, E, n_maisons_exposees, valeur_bati_exposee_eur,
      prix_median_maison_eur_m2, basculement_2026, ip_rga_score, ip_rga_niveau,
      last_updated_admin_express, last_updated_rga, last_updated_bascule, last_updated_insee,
      last_updated_fideli, last_updated_dvf, last_updated_swi)"""
_MENSUEL_OLD = """SELECT * FROM (VALUES ('31555', DATE '2025-12-01', -1.2, 0.77, 0.77, 45,
    'Élevée', 4)) t(insee, date_mois, z_swi, dry_swi, T, ip_rga_score, ip_rga_niveau,
    ip_rga_niveau_code)"""


def test_api_tolerant_to_old_mart_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mart = tmp_path / "commune_pression.parquet"
    mensuel = tmp_path / "commune_pression_mensuel.parquet"
    with duckdb_io.connection() as con:
        con.execute(f"COPY ({_MART_OLD}) TO '{mart}' (FORMAT PARQUET)")
        con.execute(f"COPY ({_MENSUEL_OLD}) TO '{mensuel}' (FORMAT PARQUET)")
    monkeypatch.setattr(deps, "mart_path", lambda: mart)
    monkeypatch.setattr(deps, "mensuel_path", lambda: mensuel)
    monkeypatch.setattr(deps, "seuils_path", lambda: tmp_path / "absent.json")
    client = TestClient(app)
    # fiche : pas de 500, et les champs IPS absents ⇒ None (pas d'erreur de colonne)
    fiche = client.get("/communes/31555")
    assert fiche.status_code == 200
    assert fiche.json()["ip_rga_score"] == 45 and fiche.json()["ips_classe"] is None
    # /meta : pas de 500, last_updated_ips ⇒ None
    meta = client.get("/meta")
    assert meta.status_code == 200 and meta.json()["last_updated_ips"] is None
