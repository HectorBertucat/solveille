"""Test déterministe des agrégats DVF par commune (dédoublonnage mutation, mono-bien).

Mutations dans la commune 99001 :
- M1 : 1 maison 100 m² à 200 000 € → 2000 €/m², mono-bien, dans les 12 mois → gardée
- M2 : maison + appartement (multi-local) → exclue
- M3 : maison 50 m² à 5 000 000 € → 100 000 €/m², aberrant → exclu
- M4 : maison 80 m² + dépendance à 160 000 € → 2000 €/m², mono-bien, hors 12 mois → gardée
- M5 : 2 lignes maison (multi-parcelle) → n_maison=2 → exclue (filtre conservateur)
Attendu : médiane 2000 €/m², surface médiane 90 m², 2 transactions, 1 dans les 12 mois.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from solveille.common import duckdb_io
from solveille.transform.commune_dvf import build_commune_dvf

_CSV = """id_mutation,date_mutation,valeur_fonciere,code_commune,type_local,surface_reelle_bati
M1,2025-06-01,200000,99001,Maison,100
M2,2025-05-02,300000,99001,Maison,90
M2,2025-05-02,300000,99001,Appartement,40
M3,2025-05-03,5000000,99001,Maison,50
M4,2024-01-15,160000,99001,Maison,80
M4,2024-01-15,160000,99001,Dépendance,20
M5,2025-05-01,180000,99001,Maison,90
M5,2025-05-01,180000,99001,Maison,90
"""


@pytest.fixture
def commune_dvf(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    (raw / "2025").mkdir(parents=True)
    with gzip.open(raw / "2025" / "99.csv.gz", "wt", encoding="utf-8") as fh:
        fh.write(_CSV)
    out = tmp_path / "commune_dvf.parquet"
    build_commune_dvf(raw_dir=raw, out=out)
    return out


def test_dedup_monobien_and_outliers(commune_dvf: Path) -> None:
    con = duckdb_io.connect()
    row = con.execute(
        f"""SELECT prix_median_maison_eur_m2, surface_mediane_maison_m2,
                   n_tx_maison_total, n_tx_maison_12m, annee_min, annee_max
            FROM read_parquet('{commune_dvf}') WHERE code_insee = '99001'"""
    ).fetchone()
    prix, surf, n_total, n_12m, amin, amax = row
    assert prix == pytest.approx(
        2000.0
    )  # M1 et M4 (M2 multi, M3 aberrant, M5 multi-parcelle exclus)
    assert surf == pytest.approx(90.0)  # médiane(100, 80)
    assert n_total == 2
    assert n_12m == 1  # seul M1 (2025-06-01) dans les 12 mois ; M4 (2024-01-15) hors
    assert (amin, amax) == (2024, 2025)


def test_aggregates_only_one_row_per_commune(commune_dvf: Path) -> None:
    con = duckdb_io.connect()
    n = con.execute(f"SELECT count(*) FROM read_parquet('{commune_dvf}')").fetchone()[0]
    assert n == 1  # agrégat communal unique (aucune ligne nominative)
