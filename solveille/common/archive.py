"""Décompression d'archives `.7z` (ADMIN EXPRESS) — extraction sélective de membres."""

from __future__ import annotations

from pathlib import Path

import py7zr


def extract_7z(
    archive: Path | str,
    dest_dir: Path | str,
    *,
    suffixes: tuple[str, ...] | None = None,
) -> list[Path]:
    """Extrait (sélectivement) les membres d'une archive `.7z` vers `dest_dir`.

    Si `suffixes` est fourni, n'extrait que les fichiers dont le nom se termine par
    l'un d'eux (ex. `(".gpkg",)`), pour éviter de matérialiser les PDF/SHP inutiles.
    Retourne les chemins extraits. Lève `FileNotFoundError` si aucun membre ne matche.
    """
    archive = Path(archive)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    with py7zr.SevenZipFile(archive, mode="r") as z:
        names = z.getnames()
        if suffixes:
            wanted = tuple(s.lower() for s in suffixes)
            targets = [n for n in names if n.lower().endswith(wanted)]
        else:
            targets = list(names)
        if not targets:
            raise FileNotFoundError(f"Aucun membre {suffixes} dans {archive}")
        z.extract(path=dest_dir, targets=targets)
    return [dest_dir / t for t in targets]
