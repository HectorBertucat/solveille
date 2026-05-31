"""Test du mart statique commune_pression : LEFT joins, E (gating pas-d'argile), J, flag
bascule, et fusion du **dernier mois** (T/score/niveau) depuis le mart mensuel (v1).

Commune A : aléa 0.5 + vulnérabilité 0.5 → E=0.5 ; 100 maisons × 90 m² × 2000 € →
valeur 18 M€ ; reclassée. Commune B : aléa 0 mais vulnérabilité 0.9 → E doit valoir 0
(gating) → score 0, niveau NULL. Commune C : vuln inconnue → fallback surfacique. D : clamp à 1.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from solveille.common import duckdb_io
from solveille.metric.ip_rga import dry_intensity, exposition_e, ip_rga_score
from solveille.transform.mart import build_commune_pression, build_commune_pression_mensuel

# A : exposé + prix → E et valeur. B : aléa 0 + vuln 0.9 → E gaté à 0. C : aléa>0 mais vuln
# inconnue (absente de commune_stock) → fallback surfacique. D : aléa 1 + vuln 1 → clamp à 1.
_TABLES = {
    "commune": """SELECT * FROM (VALUES
        ('A','99','Commune A'), ('B','99','Commune B'),
        ('C','99','Commune C'), ('D','99','Commune D')
      ) t(code_insee, code_dept, nom)""",
    "commune_rga": """SELECT * FROM (VALUES
        ('A', 0.3, 0.2, 0.5, TRUE, 'Moyen'),
        ('B', 0.0, 0.0, 0.0, TRUE, 'Aucun'),
        ('C', 0.2, 0.2, 0.4, TRUE, 'Moyen'),
        ('D', 0.5, 0.5, 1.0, TRUE, 'Fort')
      ) t(code_insee, part_alea_moyen, part_alea_fort, part_alea_moyen_fort,
          has_rga_coverage, classe_dominante)""",
    # C omise → part_maisons_vulnerables NULL (test du fallback surfacique).
    "commune_stock": """SELECT * FROM (VALUES
        ('A', 0.5, 100.0, FALSE),
        ('B', 0.9, 50.0, FALSE),
        ('D', 1.0, 200.0, FALSE)
      ) t(code_insee, part_maisons_vulnerables, n_maisons_exposees, stock_secret)""",
    "commune_dvf": """SELECT * FROM (VALUES
        ('A', 2000.0, 90.0, 10)
      ) t(code_insee, prix_median_maison_eur_m2, surface_mediane_maison_m2, n_tx_maison_12m)""",
    "commune_bascule": """SELECT * FROM (VALUES
        ('A', 2, 3, 'moyenne_vers_forte', TRUE)
      ) t(code_insee, rga_classe_2020, rga_classe_2026, bascule_type, basculement_2026)""",
    # Dernier mois servi : 2024-08. z_SWI par commune (sec si <0).
    "commune_swi": """SELECT * FROM (VALUES
        ('A', DATE '2024-08-01', -1.0, 1),
        ('B', DATE '2024-08-01', -1.0, 1),
        ('C', DATE '2024-08-01',  0.5, 1),
        ('D', DATE '2024-08-01', -2.0, 1)
      ) t(code_insee, date_mois, z_swi, n_mailles)""",
}


@pytest.fixture
def mart(tmp_path: Path) -> Path:
    stg = tmp_path / "staging"
    stg.mkdir()
    with duckdb_io.connection() as con:
        for name, sql in _TABLES.items():
            con.execute(f"COPY ({sql}) TO '{stg / (name + '.parquet')}' (FORMAT PARQUET)")
    mensuel = tmp_path / "commune_pression_mensuel.parquet"
    build_commune_pression_mensuel(
        staging_dir=stg, out=mensuel, seuils_out=tmp_path / "seuils.json"
    )
    out = tmp_path / "commune_pression.parquet"
    build_commune_pression(staging_dir=stg, out=out, mensuel=mensuel)
    return out


def _row(path: Path, insee: str) -> dict:
    con = duckdb_io.connect()
    cols = [c[0] for c in con.execute(f"SELECT * FROM read_parquet('{path}') LIMIT 0").description]
    vals = con.execute(f"SELECT * FROM read_parquet('{path}') WHERE insee = '{insee}'").fetchone()
    return dict(zip(cols, vals, strict=True))


def test_commune_exposed_with_value(mart: Path) -> None:
    a = _row(mart, "A")
    assert a["E"] == pytest.approx(0.6 * 0.5 + 0.4 * 0.5)  # 0.5
    assert a["valeur_bati_exposee_eur"] == pytest.approx(100 * 90 * 2000)
    assert a["basculement_2026"] is True
    # dernier mois fusionné : score = round(100·E·T^γ), T = dry_SWI(z=-1) (sec)
    assert a["ip_rga_score"] == ip_rga_score(0.5, dry_intensity(-1.0))
    assert a["ip_rga_score"] > 0 and a["ip_rga_niveau"] is not None
    assert str(a["date"]) == "2024-08-01"


def test_no_clay_gates_e_to_zero(mart: Path) -> None:
    b = _row(mart, "B")
    assert b["E"] == 0.0  # aléa 0 → E=0 malgré vulnérabilité 0.9 (cas Paris)
    assert b["valeur_bati_exposee_eur"] is None  # pas de prix DVF
    assert b["basculement_2026"] is False  # absente de la table bascule → COALESCE FALSE
    assert b["ip_rga_score"] == 0 and b["ip_rga_niveau"] is None  # E=0 ⇒ 0, pas de niveau


def test_vuln_unknown_falls_back_to_surface(mart: Path) -> None:
    c = _row(mart, "C")
    assert c["E"] == pytest.approx(0.4)  # vuln NULL → E = part_alea_moyen_fort
    assert c["valeur_bati_exposee_eur"] is None


def test_clamp_to_one(mart: Path) -> None:
    d = _row(mart, "D")
    assert d["E"] == pytest.approx(1.0)  # 0.6·1 + 0.4·1 borné à 1


def test_sql_matches_pure_function(mart: Path) -> None:
    """Parité réplique SQL ↔ fonction de référence (verrou anti-désync des poids)."""
    cases = {"A": (0.5, 0.5), "B": (0.0, 0.9), "C": (0.4, None), "D": (1.0, 1.0)}
    for insee, (pmf, vuln) in cases.items():
        assert _row(mart, insee)["E"] == pytest.approx(exposition_e(pmf, vuln))


def test_all_communes_present(mart: Path) -> None:
    con = duckdb_io.connect()
    n = con.execute(f"SELECT count(*) FROM read_parquet('{mart}')").fetchone()[0]
    assert n == 4


# --- v2 : calibration H branchée au mart (commune_h + catnat_secheresse présents) ---------

_TABLES_H = {
    **_TABLES,
    # H par commune×mois (E>0 : A,D exposées ; B aléa 0 ⇒ E=0 ⇒ H gaté NULL au mart).
    "commune_h": """SELECT * FROM (VALUES
        ('A', DATE '2024-08-01', 0.70::DOUBLE, 50::BIGINT, 'departement'),
        ('B', DATE '2024-08-01', 0.40::DOUBLE, 50::BIGINT, 'departement'),
        ('D', DATE '2024-08-01', 0.90::DOUBLE, 50::BIGINT, 'national')
      ) t(code_insee, date_mois, h_proba, h_n_events, h_pool_level)""",
    # Historique des arrêtés (le mart ne lit que catnat_freq/dernier_arrete/annees_reco).
    "catnat_secheresse": """SELECT * FROM (VALUES
        ('A', 3, DATE '2022-12-01', [2003, 2018, 2022]),
        ('D', 1, DATE '2017-12-01', [2017])
      ) t(code_insee, catnat_freq, dernier_arrete, annees_reco)""",
}


def _mensuel_static(tables: dict[str, str], tmp_path: Path) -> tuple[Path, Path]:
    stg = tmp_path / "staging"
    stg.mkdir(exist_ok=True)
    with duckdb_io.connection() as con:
        for name, sql in tables.items():
            con.execute(f"COPY ({sql}) TO '{stg / (name + '.parquet')}' (FORMAT PARQUET)")
    mensuel = tmp_path / "commune_pression_mensuel.parquet"
    build_commune_pression_mensuel(staging_dir=stg, out=mensuel, seuils_out=tmp_path / "s.json")
    static = tmp_path / "commune_pression.parquet"
    build_commune_pression(staging_dir=stg, out=static, mensuel=mensuel)
    return mensuel, static


def test_mart_h_columns_and_gating(tmp_path: Path) -> None:
    mensuel, static = _mensuel_static(_TABLES_H, tmp_path)
    a_m = _row(mensuel, "A")
    assert a_m["h_proba"] == pytest.approx(0.70)  # E>0 → H exposé
    assert a_m["h_n_events"] == 50 and a_m["h_pool_level"] == "departement"
    b_m = _row(mensuel, "B")
    assert b_m["h_proba"] is None  # E=0 ⇒ H gaté NULL (comme ip_rga_niveau)
    a_s = _row(static, "A")
    assert a_s["H_latest"] == pytest.approx(0.70)  # dernier mois
    assert a_s["catnat_freq"] == 3 and str(a_s["dernier_arrete"]) == "2022-12-01"
    assert list(a_s["annees_reco"]) == [2003, 2018, 2022]
    b_s = _row(static, "B")
    assert b_s["H_latest"] is None and b_s["catnat_freq"] is None  # E=0 + pas d'arrêté


def test_mart_h_absent_is_null(tmp_path: Path) -> None:
    # commune_h / catnat_secheresse absents (GASPAR non ingéré) → mart valide, colonnes H NULL.
    mensuel, static = _mensuel_static(_TABLES, tmp_path)
    a_m = _row(mensuel, "A")
    assert "h_proba" in a_m and a_m["h_proba"] is None  # colonne présente, valeur NULL
    a_s = _row(static, "A")
    assert a_s["H_latest"] is None and a_s["catnat_freq"] is None and a_s["annees_reco"] is None
