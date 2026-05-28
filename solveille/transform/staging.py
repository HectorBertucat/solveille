"""Transformations staging : raw → `data/staging/*.parquet` (typage, EPSG:2154, validation).

Chaque fonction `build_*` lit le brut d'une source et matérialise une table staging en
Parquet. Les géométries sont persistées en **WKB** (`geom_wkb`, convention SRID 2154) pour
éviter les pièges de métadonnées GeoParquet ; on les recharge via `ST_GeomFromWKB`.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from solveille.common import duckdb_io
from solveille.common.archive import extract_7z
from solveille.common.config import get_settings
from solveille.common.logging import get_logger

log = get_logger("solveille.transform.staging")

#: Nom de la couche communale dans le GeoPackage ADMIN EXPRESS COG CARTO v4.
ADMIN_EXPRESS_LAYER = "commune"


def _find_or_extract_admin_express_gpkg() -> Path:
    """Trouve le GeoPackage ADMIN EXPRESS extrait, sinon le décompresse depuis le brut."""
    s = get_settings()
    staging_ae = s.staging_dir / "admin_express"
    existing = sorted(staging_ae.rglob("*.gpkg"))
    if existing:
        return existing[-1]
    archives = sorted(s.source_raw_dir("admin_express").rglob("*.7z"))
    if not archives:
        raise FileNotFoundError(
            "Archive ADMIN EXPRESS absente — lance d'abord `make fetch-communes`."
        )
    log.info("staging.extract_gpkg", archive=str(archives[-1]))
    files = extract_7z(archives[-1], staging_ae, suffixes=(".gpkg",))
    gpkgs = [f for f in files if f.suffix.lower() == ".gpkg"]
    if not gpkgs:
        raise FileNotFoundError("Aucun .gpkg dans l'archive ADMIN EXPRESS.")
    return gpkgs[-1]


def build_commune(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    gpkg: Path | None = None,
    out: Path | None = None,
    departements: list[str] | None = None,
) -> Path:
    """Construit `data/staging/commune.parquet` depuis le GPKG ADMIN EXPRESS (EPSG:2154).

    Borne aux départements configurés (`SOLVEILLE_DEPARTEMENTS`) si non vide, sinon
    national. Normalise les clés en VARCHAR (zéros, Corse 2A/2B) et valide la géométrie
    (`ST_MakeValid`) avant de la sérialiser en WKB.

    `gpkg`/`out`/`departements` sont surchargeables (tests offline sur fixture).
    """
    s = get_settings()
    gpkg = gpkg or _find_or_extract_admin_express_gpkg()
    out = out or (s.staging_dir / "commune.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    deps = s.departements if departements is None else departements
    where = ""
    if deps:
        lst = ", ".join(f"'{d}'" for d in deps)
        where = f"WHERE code_insee_du_departement IN ({lst})"

    own = con is None
    con = con or duckdb_io.connect()
    try:
        con.execute(
            f"""
            COPY (
              SELECT
                code_insee::VARCHAR                              AS code_insee,
                nom_officiel::VARCHAR                            AS nom,
                code_insee_du_departement::VARCHAR               AS code_dept,
                NULLIF(trim(codes_siren_des_epci), '')::VARCHAR  AS siren_epci,
                NULLIF(trim(code_siren), '')::VARCHAR            AS code_siren,
                TRY_CAST(population AS BIGINT)                   AS population,
                ST_AsWKB(ST_MakeValid(geometrie))               AS geom_wkb
              FROM ST_Read('{gpkg}', layer='{ADMIN_EXPRESS_LAYER}')
              {where}
            ) TO '{out}' (FORMAT PARQUET);
            """
        )
        n = duckdb_io.scalar(con, f"SELECT count(*) FROM read_parquet('{out}')")
    finally:
        if own:
            con.close()
    log.info("staging.commune", path=str(out), n_communes=n, bornage=deps or "national")
    return out
