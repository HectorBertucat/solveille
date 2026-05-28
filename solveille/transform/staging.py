"""Transformations staging : raw → `data/staging/*.parquet` (typage, EPSG:2154, validation).

Chaque fonction `build_*` lit le brut d'une source et matérialise une table staging en
Parquet. Les géométries sont persistées en **WKB** (`geom_wkb`, convention SRID 2154) pour
éviter les pièges de métadonnées GeoParquet ; on les recharge via `ST_GeomFromWKB`.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from solveille.common import duckdb_io
from solveille.common.archive import extract_7z, extract_zip
from solveille.common.config import get_settings
from solveille.common.logging import get_logger

log = get_logger("solveille.transform.staging")

#: Nom de la couche communale dans le GeoPackage ADMIN EXPRESS COG CARTO v4.
ADMIN_EXPRESS_LAYER = "commune"
#: Répertoire brut de la source RGA 2026.
SOURCE_RGA = "rga_2026"
#: Répertoire brut de la source « communes basculées 2026 ».
SOURCE_BASCULE = "communes_bascule"
#: Répertoire brut de la source INSEE logement.
SOURCE_INSEE = "insee_logement"
#: Répertoire brut de la source Fideli/SDES (exposition maisons par EPCI).
SOURCE_FIDELI = "fideli_epci"

# Codes d'arrondissements municipaux (Paris/Lyon/Marseille) à exclure pour éviter le
# double comptage avec la commune entière (75056 / 69123 / 13055).
_PLM_ARRONDISSEMENTS_FILTER = (
    "CODGEO NOT BETWEEN '75101' AND '75120' "
    "AND CODGEO NOT BETWEEN '69381' AND '69389' "
    "AND CODGEO NOT BETWEEN '13201' AND '13216'"
)


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


def build_rga(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    raw_dir: Path | None = None,
    out: Path | None = None,
    simplify_tolerance_m: float = 25.0,
) -> Path:
    """Construit `data/staging/rga.parquet` depuis les GeoJSON RGA 2026.

    Reprojette 4326 → EPSG:2154 (**`always_xy:=true`** obligatoire : la sortie ArcGIS est
    en lon/lat), simplifie en préservant la topologie (`simplify_tolerance_m`, défaut 25 m :
    ~5× moins de sommets pour ~1 % d'écart d'aire — nécessaire pour tenir les intersections
    dans 8 Go de RAM ; impact négligeable à l'échelle commune pour un indice indicatif),
    valide la géométrie, conserve `code_dept`, `niveau` (1/2/3), `alea`.
    """
    s = get_settings()
    raw_dir = raw_dir or s.source_raw_dir(SOURCE_RGA)
    out = out or (s.staging_dir / "rga.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in raw_dir.glob("*.geojson") if not p.name.startswith("_"))
    if not files:
        raise FileNotFoundError("GeoJSON RGA absents — lance d'abord `make fetch-rga`.")

    own = con is None
    con = con or duckdb_io.connect()
    try:
        # Insertion fichier par fichier : borne le pic mémoire (un GeoJSON dept à la fois).
        con.execute(
            "CREATE OR REPLACE TEMP TABLE _rga_stg "
            "(code_dept VARCHAR, niveau INTEGER, alea VARCHAR, geom_wkb BLOB)"
        )
        for f in files:
            con.execute(
                f"""
                INSERT INTO _rga_stg
                SELECT DPT::VARCHAR, CAST(NIVEAU AS INTEGER), ALEA::VARCHAR,
                       ST_AsWKB(ST_MakeValid(ST_SimplifyPreserveTopology(
                         ST_Transform(geom, 'EPSG:4326', 'EPSG:2154', always_xy := true),
                         {simplify_tolerance_m})))
                FROM ST_Read('{f}')
                """
            )
        con.execute(f"COPY _rga_stg TO '{out}' (FORMAT PARQUET);")
        n = duckdb_io.scalar(con, f"SELECT count(*) FROM read_parquet('{out}')")
    finally:
        if own:
            con.close()
    log.info("staging.rga", path=str(out), n_features=n, n_files=len(files))
    return out


def build_commune_bascule(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    csv: Path | None = None,
    out: Path | None = None,
) -> Path:
    """Construit `data/staging/commune_bascule.parquet` (flag reclassement 2026).

    Le fichier ne liste que les communes **qui changent** de classe → `basculement_2026`
    vaut TRUE pour toutes les lignes. `code_insee` conservé en VARCHAR (zéros, Corse).
    """
    s = get_settings()
    if csv is None:
        candidates = sorted(s.source_raw_dir(SOURCE_BASCULE).glob("*.csv"))
        if not candidates:
            raise FileNotFoundError("CSV bascule absent — lance d'abord `make fetch-bascule`.")
        csv = candidates[-1]
    out = out or (s.staging_dir / "commune_bascule.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)

    own = con is None
    con = con or duckdb_io.connect()
    try:
        con.execute(
            f"""
            COPY (
              SELECT code_insee::VARCHAR                    AS code_insee,
                     TRY_CAST(rga_classe_2020 AS INTEGER)   AS rga_classe_2020,
                     TRY_CAST(rga_classe_2026 AS INTEGER)   AS rga_classe_2026,
                     bascule_type::VARCHAR                  AS bascule_type,
                     TRUE                                   AS basculement_2026
              FROM read_csv('{csv}', header = true, all_varchar = true)
            ) TO '{out}' (FORMAT PARQUET);
            """
        )
        n = duckdb_io.scalar(con, f"SELECT count(*) FROM read_parquet('{out}')")
    finally:
        if own:
            con.close()
    log.info("staging.commune_bascule", path=str(out), n_communes=n)
    return out


def build_commune_logement(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    zip_path: Path | None = None,
    csv: Path | None = None,
    out: Path | None = None,
) -> Path:
    """Construit `data/staging/commune_logement.parquet` (parc de maisons par commune).

    Décompresse le CSV INSEE (`;`, UTF-8), garde `P22_MAISON/APPART/LOG` (DOUBLE, valeurs
    estimées par sondage), exclut les arrondissements municipaux PLM (anti double-comptage).
    `csv` (chemin direct) court-circuite la décompression — utile pour les tests offline.
    """
    s = get_settings()
    if csv is None:
        if zip_path is None:
            zips = sorted(s.source_raw_dir(SOURCE_INSEE).rglob("*.zip"))
            if not zips:
                raise FileNotFoundError("Zip INSEE absent — lance d'abord `make fetch-insee`.")
            zip_path = zips[-1]
        members = extract_zip(zip_path, s.staging_dir / SOURCE_INSEE, suffixes=(".csv",))
        csv = max(
            (m for m in members if "meta" not in m.name.lower()), key=lambda m: m.stat().st_size
        )
    data_csv = csv
    out = out or (s.staging_dir / "commune_logement.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)

    own = con is None
    con = con or duckdb_io.connect()
    try:
        con.execute(
            f"""
            COPY (
              SELECT CODGEO::VARCHAR                  AS code_insee,
                     TRY_CAST(P22_MAISON AS DOUBLE)   AS n_maisons,
                     TRY_CAST(P22_APPART AS DOUBLE)   AS n_appart,
                     TRY_CAST(P22_LOG AS DOUBLE)      AS n_logements
              FROM read_csv('{data_csv}', delim = ';', header = true, all_varchar = true)
              WHERE {_PLM_ARRONDISSEMENTS_FILTER}
            ) TO '{out}' (FORMAT PARQUET);
            """
        )
        n = duckdb_io.scalar(con, f"SELECT count(*) FROM read_parquet('{out}')")
    finally:
        if own:
            con.close()
    log.info("staging.commune_logement", path=str(out), n_communes=n)
    return out


def _fideli_csv(name: str, csv: Path | None) -> Path:
    if csv is not None:
        return csv
    p = get_settings().source_raw_dir(SOURCE_FIDELI) / name
    if not p.exists():
        raise FileNotFoundError(f"{name} absent — lance d'abord `make fetch-fideli`.")
    return p


def build_epci_stock(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    csv: Path | None = None,
    out: Path | None = None,
) -> Path:
    """Construit `data/staging/epci_stock.parquet` (maisons + surfaces exposées par EPCI).

    Pivote le CSV long (`INDICATEUR_EXPOSITION`) ; `secret` (secret statistique) → NULL,
    avec un flag `has_secret`. `siren_epci` = SIREN 9 chiffres (VARCHAR).
    """
    s = get_settings()
    src = _fideli_csv("fideli_par_epci.csv", csv)
    out = out or (s.staging_dir / "epci_stock.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)

    def _ind(label: str, typ: str) -> str:
        return (
            f"TRY_CAST(MAX(VALEUR_EXPOSITION) FILTER "
            f"(WHERE INDICATEUR_EXPOSITION = '{label}') AS {typ})"
        )

    own = con is None
    con = con or duckdb_io.connect()
    try:
        con.execute(
            f"""
            COPY (
              SELECT EPCI_CODE::VARCHAR AS siren_epci,
                     any_value(EPCI_LIBELLE) AS nom_epci,
                     {_ind("Maisons_indiv_exposees_RGA1", "BIGINT")} AS maisons_rga1,
                     {_ind("Maisons_indiv_exposees_RGA2", "BIGINT")} AS maisons_rga2,
                     {_ind("Maisons_indiv_exposees_RGA3", "BIGINT")} AS maisons_rga3,
                     {_ind("Surface_exposee_RGA1_km2", "DOUBLE")} AS surface_rga1_km2,
                     {_ind("Surface_exposee_RGA2_km2", "DOUBLE")} AS surface_rga2_km2,
                     {_ind("Surface_exposee_RGA3_km2", "DOUBLE")} AS surface_rga3_km2,
                     bool_or(VALEUR_EXPOSITION = 'secret') AS has_secret
              FROM read_csv('{src}', delim = ';', header = true, all_varchar = true)
              GROUP BY EPCI_CODE
            ) TO '{out}' (FORMAT PARQUET);
            """
        )
        n = duckdb_io.scalar(con, f"SELECT count(*) FROM read_parquet('{out}')")
        nsecret = duckdb_io.scalar(
            con, f"SELECT count(*) FILTER (WHERE has_secret) FROM read_parquet('{out}')"
        )
    finally:
        if own:
            con.close()
    log.info("staging.epci_stock", path=str(out), n_epci=n, n_avec_secret=nsecret)
    return out


def build_epci_stock_periode(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    csv: Path | None = None,
    out: Path | None = None,
) -> Path:
    """Construit `data/staging/epci_stock_periode.parquet` (maisons exposées par période).

    Sert à pondérer la vulnérabilité du bâti (part construite avant ~1990). `secret` → NULL.
    """
    s = get_settings()
    src = _fideli_csv("fideli_par_periode.csv", csv)
    out = out or (s.staging_dir / "epci_stock_periode.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)

    own = con is None
    con = con or duckdb_io.connect()
    try:
        con.execute(
            f"""
            COPY (
              SELECT EPCI_CODE::VARCHAR                            AS siren_epci,
                     PERIODE_CONSTRUCTION::VARCHAR                 AS periode_construction,
                     TRY_CAST(MAISONS_INDIV_EXPOSES_RGA1 AS BIGINT) AS maisons_rga1,
                     TRY_CAST(MAISONS_INDIV_EXPOSES_RGA2 AS BIGINT) AS maisons_rga2,
                     TRY_CAST(MAISONS_INDIV_EXPOSES_RGA3 AS BIGINT) AS maisons_rga3
              FROM read_csv('{src}', delim = ';', header = true, all_varchar = true)
            ) TO '{out}' (FORMAT PARQUET);
            """
        )
        n = duckdb_io.scalar(con, f"SELECT count(*) FROM read_parquet('{out}')")
    finally:
        if own:
            con.close()
    log.info("staging.epci_stock_periode", path=str(out), n_lignes=n)
    return out
