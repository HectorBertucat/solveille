"""Staging piézo : reprojection WGS84→EPSG:2154 des stations et médiane mensuelle du NGF.

Fixtures NDJSON (1 enregistrement/ligne), comme le brut écrit par `ingest.hubeau_piezo`.
"""

from __future__ import annotations

import json
from pathlib import Path

from solveille.common import duckdb_io
from solveille.common.geo import METROPOLE_L93_BBOX
from solveille.transform import piezo


def _write_ndjson(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def test_build_piezo_stations_reprojects_to_l93(tmp_path: Path) -> None:
    sdir = tmp_path / "stations"
    _write_ndjson(
        sdir / "34.jsonl",
        [
            {  # ~Montpellier : lon=x, lat=y (WGS84) → doit tomber dans la bbox métropole L93
                "code_bss": "10001X0001/P",
                "code_commune_insee": "34172",
                "code_departement": "34",
                "x": 3.65,
                "y": 43.47,
                "date_debut_mesure": "2000-01-01",
                "date_fin_mesure": "2024-01-01",
                "nb_mesures_piezo": 8000,
                "profondeur_investigation": 30.0,
                "codes_bdlisa": ["123AB"],
            },
            {"code_bss": "10001X0002/P", "x": None, "y": None},  # coords nulles → filtrée
        ],
    )
    con = duckdb_io.connect()
    try:
        out = piezo.build_piezo_stations(
            con, raw_dir=sdir, out=tmp_path / "piezo_stations.parquet"
        )
        row = con.execute(
            f"""SELECT code_bss, code_commune_insee, round(span_annees, 2),
                       round(ST_X(ST_GeomFromWKB(geom_wkb))), round(ST_Y(ST_GeomFromWKB(geom_wkb)))
                FROM read_parquet('{out}')"""
        ).fetchall()
    finally:
        con.close()
    assert len(row) == 1  # la station sans coords est exclue
    code, insee, span, x93, y93 = row[0]
    assert code == "10001X0001/P" and insee == "34172"
    assert span == 24.0  # (2024-01-01 − 2000-01-01) / 365.25 ≈ 24 ans
    xmin, ymin, xmax, ymax = METROPOLE_L93_BBOX
    assert xmin <= x93 <= xmax and ymin <= y93 <= ymax  # reproj plausible (always_xy)


def test_build_piezo_mensuel_monthly_median(tmp_path: Path) -> None:
    cdir = tmp_path / "chroniques"
    _write_ndjson(
        cdir / "10001X0001_P.jsonl",
        [
            {"code_bss": "S1", "date_mesure": "2022-08-03", "niveau_nappe_eau": 10.0},
            {"code_bss": "S1", "date_mesure": "2022-08-12", "niveau_nappe_eau": 20.0},
            {"code_bss": "S1", "date_mesure": "2022-08-28", "niveau_nappe_eau": 30.0},
            {"code_bss": "S1", "date_mesure": "2022-09-05", "niveau_nappe_eau": 99.0},
            {"code_bss": "S1", "date_mesure": "2022-08-15", "niveau_nappe_eau": None},  # ignoré
        ],
    )
    con = duckdb_io.connect()
    try:
        out = piezo.build_piezo_mensuel(con, raw_dir=cdir, out=tmp_path / "piezo_mensuel.parquet")
        rows = {
            d: (ngf, n)
            for d, ngf, n in con.execute(
                f"SELECT strftime(date_mois, '%Y-%m-%d'), ngf, n_obs FROM read_parquet('{out}') "
                f"ORDER BY date_mois"
            ).fetchall()
        }
    finally:
        con.close()
    assert rows["2022-08-01"] == (20.0, 3)  # médiane de {10,20,30}, NULL ignoré
    assert rows["2022-09-01"] == (99.0, 1)
