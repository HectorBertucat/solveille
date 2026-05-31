"""Test de l'index de recherche communal (`front/communes-index.json`) : jointure commune + codes
postaux, **rollup PLM** (arrondissements → commune COG), bbox WGS84, niveau du dernier mois, et
dégradation propre quand les codes postaux sont absents."""

from __future__ import annotations

import json
from pathlib import Path

from solveille.common import duckdb_io
from solveille.transform.build_search import build_communes_index


# 3 communes (carrés ~1 km² en L93) : Paris 75056, Toulouse 31555, et une 3e sans CP.
def _commune_sql() -> str:
    rows = [
        ("75056", "Paris", "75", 650000, 6860000),
        ("31555", "Toulouse", "31", 573000, 6278000),
        ("09001", "Aigues-Vives", "09", 600000, 6200000),
    ]
    parts = []
    for ins, nom, dept, x, y in rows:
        poly = f"POLYGON(({x} {y},{x + 1000} {y},{x + 1000} {y + 1000},{x} {y + 1000},{x} {y}))"
        parts.append(
            f"SELECT '{ins}' AS code_insee, '{nom}' AS nom, '{dept}' AS code_dept, "
            f"ST_AsWKB(ST_GeomFromText('{poly}')) AS geom_wkb"
        )
    return " UNION ALL ".join(parts)


# Mensuel : niveau du dernier mois (2024-08 > 2024-07) → pastille.
_MENSUEL = """SELECT * FROM (VALUES
  ('75056', DATE '2024-08-01', 0), ('31555', DATE '2024-07-01', 3),
  ('31555', DATE '2024-08-01', 4), ('09001', DATE '2024-08-01', 2)
) t(insee, date_mois, ip_rga_niveau_code)"""


def _write_parquets(tmp_path: Path) -> tuple[Path, Path]:
    commune = tmp_path / "commune.parquet"
    mensuel = tmp_path / "mensuel.parquet"
    with duckdb_io.connection() as con:
        con.execute(f"COPY ({_commune_sql()}) TO '{commune}' (FORMAT PARQUET)")
        con.execute(f"COPY ({_MENSUEL}) TO '{mensuel}' (FORMAT PARQUET)")
    return commune, mensuel


def _write_cp_csv(tmp_path: Path) -> Path:
    """CSV La Poste réaliste : Latin-1, séparateur `;`, **en-tête préfixée `#`**, arrondissement
    parisien 75101 (→ doit rouler vers 75056) et Toulouse multi-CP."""
    cp = tmp_path / "codes_postaux.csv"
    cp.write_text(
        "#Code_commune_INSEE;Nom_de_la_commune;Code_postal;Libellé_d_acheminement;Ligne_5\n"
        "75101;PARIS 01;75001;PARIS;\n"
        "31555;TOULOUSE;31000;TOULOUSE;\n"
        "31555;TOULOUSE;31100;TOULOUSE;\n"
        "31555;TOULOUSE;31100;TOULOUSE;LIEU-DIT\n",  # doublon (INSEE×CP) → dédup
        encoding="latin-1",
    )
    return cp


def _load(out: Path) -> dict:
    payload = json.loads(out.read_text(encoding="utf-8"))
    data = payload["data"]
    by_insee = {ins: i for i, ins in enumerate(data["insee"])}
    return {"payload": payload, "data": data, "idx": by_insee}


def test_index_with_codes_postaux(tmp_path: Path) -> None:
    commune, mensuel = _write_parquets(tmp_path)
    cp_csv = _write_cp_csv(tmp_path)
    out = tmp_path / "communes-index.json"
    build_communes_index(commune_parquet=commune, cp_csv=cp_csv, mensuel_parquet=mensuel, out=out)
    r = _load(out)
    data, idx = r["data"], r["idx"]

    assert r["payload"]["n"] == 3
    assert set(r["payload"]["fields"]) == {"insee", "nom", "dept", "bbox", "cp", "niveau"}
    # Rollup PLM : l'arrondissement 75101 (CP 75001) doit être rattaché à la commune COG 75056.
    assert data["cp"][idx["75056"]] == ["75001"]
    # Toulouse multi-CP, dédupliqué (le doublon Ligne_5 ne crée pas de 3e entrée).
    assert data["cp"][idx["31555"]] == ["31000", "31100"]
    # Commune sans CP → liste vide (tolérée).
    assert data["cp"][idx["09001"]] == []
    # Niveau = dernier mois (Toulouse 2024-08 → 4 ; Paris → 0).
    assert data["niveau"][idx["31555"]] == 4
    assert data["niveau"][idx["75056"]] == 0
    # bbox WGS84 plausible (Toulouse ~ lon 1.4, lat 43.6).
    bb = data["bbox"][idx["31555"]]
    assert 1.3 < bb[0] < 1.5 and 43.5 < bb[1] < 43.7


def test_index_without_codes_postaux(tmp_path: Path) -> None:
    """CP absent (pas de fetch-cp) : l'index se construit quand même, `cp` vide partout."""
    commune, mensuel = _write_parquets(tmp_path)
    out = tmp_path / "communes-index.json"
    build_communes_index(
        commune_parquet=commune,
        cp_csv=tmp_path / "absent.csv",
        mensuel_parquet=mensuel,
        out=out,
    )
    r = _load(out)
    assert r["payload"]["n"] == 3
    assert all(cp == [] for cp in r["data"]["cp"])
    # Le niveau reste calculé (mensuel présent).
    assert r["data"]["niveau"][r["idx"]["31555"]] == 4
