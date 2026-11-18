"""Tests du connecteur GASPAR : membre CATNAT de `gaspar.zip` retrouvé qu'il soit daté ou non,
et rangé sous le nom stable `catnat_gaspar.csv` (lu par le staging)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from solveille.ingest import gaspar


def test_membre_date_ou_non() -> None:
    assert gaspar.catnat_member(["azi_gaspar.csv", "catnat_gaspar.csv"]) == "catnat_gaspar.csv"
    noms = [
        "azi_gaspar_2026-11-02.csv",
        "catnat_gaspar_2026-11-02.csv",
        "pprn_gaspar_2026-11-02.csv",
    ]
    assert gaspar.catnat_member(noms) == "catnat_gaspar_2026-11-02.csv"
    # Plusieurs millésimes : le plus récent.
    assert (
        gaspar.catnat_member(["catnat_gaspar_2026-10-05.csv", "catnat_gaspar_2026-11-02.csv"])
        == "catnat_gaspar_2026-11-02.csv"
    )


def test_membre_absent_erreur_explicite() -> None:
    with pytest.raises(FileNotFoundError, match="catnat_gaspar"):
        gaspar.catnat_member(["azi_gaspar_2026-11-02.csv", "pas_catnat_gaspar_x.csv"])


def test_extrait_sous_le_nom_stable(tmp_path: Path) -> None:
    archive = tmp_path / "gaspar.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("azi_gaspar_2026-11-02.csv", "x\n")
        z.writestr("catnat_gaspar_2026-11-02.csv", "id_gaspar;code_commune\nA;31555\n")
    (tmp_path / "catnat_gaspar.csv").write_text("ancien extrait\n", encoding="utf-8")

    out = gaspar.extract_catnat(archive, tmp_path)

    assert out == tmp_path / "catnat_gaspar.csv"
    assert out.read_text(encoding="utf-8") == "id_gaspar;code_commune\nA;31555\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["catnat_gaspar.csv", "gaspar.zip"]
