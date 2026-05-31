"""Tests du staging GASPAR → `catnat_secheresse` : filtrage sécheresse, dédup (commune ×
arrêté), INSEE texte (Corse/zéros), bornage département, robustesse casse/accents.

Données synthétiques (vérifiables à la main) : mêmes en-têtes que `catnat_gaspar.csv`
(`;`, dates ISO `YYYY-MM-DD HH:MM:SS`). On contrôle que seuls les arrêtés 'Sécheresse' sont
retenus, qu'un arrêté multi-communes / un correctif ne sont comptés **qu'une fois**, et que
fréquence + années de reconnaissance + dernier arrêté sont corrects.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from solveille.common import duckdb_io
from solveille.transform import staging

_HEADER = (
    "cod_nat_catnat;cod_commune;lib_commune;num_risque_jo;lib_risque_jo;"
    "dat_deb;dat_fin;dat_pub_arrete;dat_pub_jo;dat_maj"
)

# (cod_nat, insee, nom, num_risque, libelle, deb, fin, pub_arrete, pub_jo, maj)
_ROWS = [
    (
        "INTE1",
        "31555",
        "Toulouse",
        "SEC",
        "Sécheresse",
        "2003-08-01 00:00:00",
        "2003-09-30 00:00:00",
        "2003-12-01 00:00:00",
        "2003-12-15 00:00:00",
        "2004-01-01 00:00:00",
    ),
    (
        "INTE2",
        "31555",
        "Toulouse",
        "SEC",
        "Sécheresse",
        "2018-07-01 00:00:00",
        "2018-09-30 00:00:00",
        "2018-12-01 00:00:00",
        "2018-12-20 00:00:00",
        "2019-01-01 00:00:00",
    ),
    (
        "INTE3",
        "31555",
        "Toulouse",
        "SEC",
        "Sécheresse",
        "2022-06-01 00:00:00",
        "2022-09-30 00:00:00",
        "2022-12-01 00:00:00",
        "2022-12-22 00:00:00",
        "2023-01-01 00:00:00",
    ),
    # correctif du même arrêté, même commune → doit être dédupliqué
    (
        "INTE3",
        "31555",
        "Toulouse",
        "SEC",
        "Sécheresse",
        "2022-06-01 00:00:00",
        "2022-09-30 00:00:00",
        "2022-12-01 00:00:00",
        "2022-12-22 00:00:00",
        "2023-02-01 00:00:00",
    ),
    # même arrêté national, autre commune (Foix, dépt 09)
    (
        "INTE3",
        "09001",
        "Foix",
        "SEC",
        "Sécheresse",
        "2022-06-01 00:00:00",
        "2022-09-30 00:00:00",
        "2022-12-01 00:00:00",
        "2022-12-22 00:00:00",
        "2023-01-01 00:00:00",
    ),
    # aléa différent → exclu du filtre sécheresse
    (
        "INTE4",
        "31555",
        "Toulouse",
        "ICB",
        "Inondations et/ou Coulées de Boue",
        "2020-05-01 00:00:00",
        "2020-05-03 00:00:00",
        "2020-08-01 00:00:00",
        "2020-08-10 00:00:00",
        "2020-09-01 00:00:00",
    ),
    # Corse (INSEE 2B...) → texte préservé (pas de cast int)
    (
        "INTE5",
        "2B033",
        "Corte",
        "SEC",
        "Sécheresse",
        "2017-06-01 00:00:00",
        "2017-09-30 00:00:00",
        "2017-12-01 00:00:00",
        "2017-12-20 00:00:00",
        "2018-01-01 00:00:00",
    ),
    # libellé MAJUSCULES accentuées → doit matcher (insensible casse/accents)
    (
        "INTE6",
        "32001",
        "Auch",
        "12",
        "SÉCHERESSE",
        "2015-06-01 00:00:00",
        "2015-09-30 00:00:00",
        "2015-12-01 00:00:00",
        "2015-12-20 00:00:00",
        "2016-01-01 00:00:00",
    ),
]


@pytest.fixture
def gaspar_csv(tmp_path: Path) -> Path:
    body = _HEADER + "\n" + "".join(";".join(r) + "\n" for r in _ROWS)
    p = tmp_path / "catnat_gaspar.csv"
    p.write_text(body, encoding="utf-8")
    return p


def _rows(out: Path, cols: str, where: str = "TRUE", order: str = "code_insee") -> list[Any]:
    con = duckdb_io.connect()
    try:
        return con.execute(
            f"SELECT {cols} FROM read_parquet('{out}') WHERE {where} ORDER BY {order}"
        ).fetchall()
    finally:
        con.close()


def _build(gaspar_csv: Path, tmp_path: Path, **kw: Any) -> Path:
    kw.setdefault("commune_parquet", tmp_path / "_nocommune.parquet")  # hermétique hors orphan-test
    kw.setdefault("departements", [])  # national déterministe (indépendant de l'env)
    return staging.build_catnat_secheresse(
        raw_csv=gaspar_csv, out=tmp_path / "catnat_secheresse.parquet", **kw
    )


def test_filtre_secheresse_freq_annees(gaspar_csv: Path, tmp_path: Path) -> None:
    out = _build(gaspar_csv, tmp_path)
    freq = dict(_rows(out, "code_insee, catnat_freq"))
    assert set(freq) == {"31555", "09001", "2B033", "32001"}  # Inondations exclu
    assert freq["31555"] == 3  # INTE1/2/3 ; correctif dédupliqué ; Inondations exclu
    assert freq["09001"] == 1
    annees = _rows(out, "annees_reco", where="code_insee='31555'")[0][0]
    assert list(annees) == [2003, 2018, 2022]
    premier, dernier = _rows(out, "premier_arrete, dernier_arrete", where="code_insee='31555'")[0]
    assert str(premier) == "2003-12-01" and str(dernier) == "2022-12-01"


def test_insee_texte_corse_et_zeros(gaspar_csv: Path, tmp_path: Path) -> None:
    out = _build(gaspar_csv, tmp_path)
    codes = {r[0] for r in _rows(out, "code_insee")}
    assert "2B033" in codes and "09001" in codes  # texte préservé (zéros, Corse 2A/2B)


def test_majuscule_accent_matche(gaspar_csv: Path, tmp_path: Path) -> None:
    out = _build(gaspar_csv, tmp_path)
    codes = {r[0] for r in _rows(out, "code_insee")}
    assert "32001" in codes  # 'SÉCHERESSE' (maj. + accent) doit matcher


def test_bornage_departement(gaspar_csv: Path, tmp_path: Path) -> None:
    out = _build(gaspar_csv, tmp_path, departements=["31"])
    codes = {r[0] for r in _rows(out, "code_insee")}
    assert codes == {"31555"}  # seul le dépt 31 retenu (dept dérivé de l'INSEE)


def test_evenements_struct(gaspar_csv: Path, tmp_path: Path) -> None:
    out = _build(gaspar_csv, tmp_path)
    evts = _rows(out, "evenements", where="code_insee='31555'")[0][0]
    assert len(evts) == 3  # 1 struct par arrêté distinct (substrat de matching H, M-B)
    assert {e["annee"] for e in evts} == {2003, 2018, 2022}
    assert all({"dat_deb", "dat_fin", "annee", "cod_nat_catnat"} <= set(e) for e in evts)


def test_orphelins_cog_smoke(gaspar_csv: Path, tmp_path: Path) -> None:
    # commune.parquet ne contient que 2 des 4 communes → les 2 autres sont orphelines COG,
    # mais restent dans la sortie (la jointure ne sert qu'au log de qualité, pas à filtrer).
    commune = tmp_path / "commune.parquet"
    con = duckdb_io.connect()
    try:
        con.execute(
            f"COPY (SELECT * FROM (VALUES ('31555'), ('09001')) t(code_insee)) "
            f"TO '{commune}' (FORMAT PARQUET)"
        )
    finally:
        con.close()
    out = _build(gaspar_csv, tmp_path, commune_parquet=commune)
    codes = {r[0] for r in _rows(out, "code_insee")}
    assert codes == {"31555", "09001", "2B033", "32001"}  # orphelins conservés


def test_brut_absent_leve(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        staging.build_catnat_secheresse(
            raw_csv=tmp_path / "absent.csv", out=tmp_path / "o.parquet", departements=[]
        )
