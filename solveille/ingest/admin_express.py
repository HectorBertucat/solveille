"""Connecteur ADMIN EXPRESS COG CARTO (IGN / Géoplateforme) — géométries communes.

Résout dynamiquement la dernière ressource GeoPackage Lambert-93 France métropole
(`GPKG_LAMB93_FXX`) via le flux Atom de data.geopf.fr (jamais d'URL en dur), télécharge
l'archive `.7z` (cache conditionnel, idempotent) et écrit le manifeste. La décompression
et l'extraction de la couche COMMUNE vivent dans `transform/`.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

from solveille.common import http
from solveille.common.config import get_settings
from solveille.common.logging import get_logger
from solveille.common.raw import RawDataset, write_manifest

log = get_logger("solveille.ingest.admin_express")

SOURCE = "admin_express"
BASE = "https://data.geopf.fr/telechargement"
RESOURCE = "ADMIN-EXPRESS-COG-CARTO"
NS = {
    "a": "http://www.w3.org/2005/Atom",
    "g": "https://data.geopf.fr/annexes/ressources/xsd/gpf_dl.xsd",
}


@dataclass
class AdminExpressResource:
    """Ressource résolue (dernière édition pour un format/CRS/zone donnés)."""

    name: str
    version: str
    fmt: str
    crs: str
    zone: str
    edition_date: str
    download_url: str
    length: int | None
    md5: str | None


def _feed_url(page: int) -> str:
    return f"{BASE}/resource/{RESOURCE}?page={page}"


def _parse_title(title: str) -> tuple[str, str, str, str, str] | None:
    """`ADMIN-EXPRESS-COG-CARTO_4-0__GPKG_LAMB93_FXX_2026-01-01` → (ver, fmt, crs, zone, date)."""
    if "__" not in title:
        return None
    left, right = title.split("__", 1)
    parts = right.split("_")
    if len(parts) != 4:
        return None
    fmt, crs, zone, date = parts
    version = left.rsplit("_", 1)[-1]
    return version, fmt, crs, zone, date


def resolve_latest(
    *, fmt: str = "GPKG", crs: str = "LAMB93", zone: str = "FXX"
) -> AdminExpressResource:
    """Sélectionne la dernière édition (format/CRS/zone) via le flux Géoplateforme.

    Parcourt toutes les pages du flux Atom, filtre les entrées correspondantes, prend
    la date d'édition la plus récente (puis la version la plus haute), et résout le lien
    `.7z` exact (+ taille + MD5) dans le sous-flux de la ressource.
    """
    first = http.get_text(_feed_url(1))
    root = ET.fromstring(first)
    pagecount = int(root.get(f"{{{NS['g']}}}pagecount", "1"))
    pages_xml = [first] + [http.get_text(_feed_url(p)) for p in range(2, pagecount + 1)]

    candidates: list[tuple[str, str, str]] = []  # (edition_date, version, name)
    for xml in pages_xml:
        r = ET.fromstring(xml)
        for entry in r.findall("a:entry", NS):
            title = (entry.findtext("a:title", default="", namespaces=NS) or "").strip()
            parsed = _parse_title(title)
            if parsed and parsed[1:4] == (fmt, crs, zone):
                ver, _f, _c, _z, date = parsed
                candidates.append((date, ver, title))
    if not candidates:
        raise LookupError(f"Aucune ressource {fmt}/{crs}/{zone} dans le flux {RESOURCE}")
    candidates.sort(reverse=True)  # date desc, puis version desc
    edition_date, version, name = candidates[0]

    sub = ET.fromstring(http.get_text(f"{BASE}/resource/{RESOURCE}/{name}"))
    download_url: str | None = None
    md5: str | None = None
    length: int | None = None
    for entry in sub.findall("a:entry", NS):
        for link in entry.findall("a:link", NS):
            if link.get("type") == "application/x-7z-compressed":
                download_url = link.get("href")
                lv = link.get(f"{{{NS['g']}}}length")
                length = int(lv) if lv else None
        md5 = entry.findtext("a:content", namespaces=NS) or md5
    if not download_url:  # repli : URL déterministe
        download_url = f"{BASE}/download/{RESOURCE}/{name}/{name}.7z"
    return AdminExpressResource(
        name, version, fmt, crs, zone, edition_date, download_url, length, md5
    )


def fetch() -> RawDataset:
    """Télécharge l'archive `.7z` de la dernière édition GPKG L93 métropole."""
    s = get_settings()
    res = resolve_latest()
    root = s.source_raw_dir(SOURCE) / res.edition_date
    dest = root / f"{res.name}.7z"
    log.info(
        "admin_express.resolve",
        name=res.name,
        edition=res.edition_date,
        url=res.download_url,
        length=res.length,
    )
    result = http.download(res.download_url, dest)
    log.info(
        "admin_express.download", status=result.status, bytes=result.n_bytes, sha256=result.sha256
    )
    manifest = write_manifest(
        SOURCE,
        root,
        source_url=res.download_url,
        srs="EPSG:2154",
        source_version=f"{res.name} (édition {res.edition_date}, v{res.version})",
        files=[dest],
        extra={
            "format": res.fmt,
            "crs": res.crs,
            "zone": res.zone,
            "edition_date": res.edition_date,
            "md5_source": res.md5,
            "download_status": result.status,
            "licence": "Licence Ouverte 2.0 (Etalab) — © IGN ADMIN EXPRESS COG CARTO",
        },
    )
    return RawDataset(SOURCE, root, [dest], manifest)


def main() -> None:
    ds = fetch()
    log.info(
        "admin_express.done",
        files=[str(f) for f in ds.files],
        manifest=str(ds.manifest_path),
    )


if __name__ == "__main__":
    main()
