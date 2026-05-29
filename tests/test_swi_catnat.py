"""Tests unitaires du connecteur SWI CatNat (classification de ressources, offline)."""

from __future__ import annotations

from solveille.ingest.swi_catnat import GRILLE_FILENAME, _classify


def _res(title: str, fmt: str, url: str, latest: str = "") -> dict:
    return {"title": title, "format": fmt, "url": url, "latest": latest}


def test_classify_decade_csv_gz() -> None:
    r = _res(
        "swi.202001-202512.csv",
        "csv.gz",
        "https://static.data.gouv.fr/resources/x/20260310-100851/swi.202001-202512.csv.gz",
        latest="https://www.data.gouv.fr/api/1/datasets/r/eec92fc2",
    )
    name, url = _classify(r)
    assert name == "swi.202001-202512.csv.gz"  # basename CDN stable pour le glob staging
    assert url == "https://www.data.gouv.fr/api/1/datasets/r/eec92fc2"  # via latest (302→CDN)


def test_classify_grille() -> None:
    r = _res(
        "caracteristiques-geographiques-mailles-swi.csv",
        "csv",
        "https://static.data.gouv.fr/resources/x/2026/modeles-caracteristiques-geographiques-mailles-swi-20260316.csv",
        latest="https://www.data.gouv.fr/api/1/datasets/r/77383638",
    )
    name, url = _classify(r)
    assert name == GRILLE_FILENAME  # nom local fixe (basename CDN horodaté)
    assert url.endswith("/77383638")


def test_classify_skips_pdf() -> None:
    r = _res(
        "presentation_SWI.pdf",
        "pdf",
        "https://static.data.gouv.fr/resources/x/2026/modeles-presentation-swi.pdf",
    )
    assert _classify(r) is None


def test_classify_fallback_on_title_when_basename_changes() -> None:
    # Basename inattendu mais format csv.gz + 'swi' dans le titre → filet de sécurité.
    r = _res("SWI.201001-201912.csv", "csv.gz", "https://example/blob?x=1")
    name, _ = _classify(r)
    assert name == "swi.201001-201912.csv.gz"
