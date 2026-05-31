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
from solveille.common.config import SWI_SERVED_FROM, get_settings
from solveille.common.geo import METROPOLE_L93_BBOX, dept_expr_from_insee
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
#: Répertoire brut de la source SWI CatNat (humidité des sols mensuelle).
SOURCE_SWI = "swi_catnat"
#: Répertoire brut de la source GASPAR (arrêtés Cat-Nat, calibration `H`).
SOURCE_GASPAR = "gaspar"

#: Dérive le code département depuis un code INSEE commune (Corse 2A/2B, DROM 97x/98x).
_DEPT_FROM_INSEE = dept_expr_from_insee("code_insee")

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


def build_swi_grille(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    grille_csv: Path | None = None,
    out: Path | None = None,
) -> Path:
    """Construit `data/staging/swi_grille.parquet` (centroïde L93 de chaque maille 8 km).

    Le fichier grille a 5 lignes de commentaire `#` (dont l'en-tête) → `comment='#'`,
    `header=false`, colonnes positionnelles : `column0`=num_maille, `column3`=lambx93,
    `column4`=lamby93 (en **mètres** L93 — l'en-tête dit « hectomètres » mais c'est faux pour
    les colonnes lamb*93*). Garde-fou SRS : alerte si un centroïde sort de l'emprise métropole.
    """
    s = get_settings()
    grille_csv = grille_csv or (s.source_raw_dir(SOURCE_SWI) / "grille_mailles.csv")
    if not grille_csv.exists():
        raise FileNotFoundError("Grille SWI absente — lance d'abord `make fetch-swi`.")
    out = out or (s.staging_dir / "swi_grille.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    xmin, ymin, xmax, ymax = METROPOLE_L93_BBOX

    own = con is None
    con = con or duckdb_io.connect()
    try:
        con.execute(
            f"""
            COPY (
              SELECT CAST(column0 AS INTEGER) AS num_maille,
                     CAST(column3 AS DOUBLE)  AS x93,
                     CAST(column4 AS DOUBLE)  AS y93
              FROM read_csv('{grille_csv}', delim = ';', header = false, comment = '#')
            ) TO '{out}' (FORMAT PARQUET);
            """
        )
        n = duckdb_io.scalar(con, f"SELECT count(*) FROM read_parquet('{out}')")
        out_of_box = duckdb_io.scalar(
            con,
            f"""SELECT count(*) FROM read_parquet('{out}')
                WHERE x93 NOT BETWEEN {xmin} AND {xmax}
                   OR y93 NOT BETWEEN {ymin} AND {ymax}""",
        )
    finally:
        if own:
            con.close()
    if out_of_box:
        log.warning("staging.swi_grille.out_of_metropole_bbox", n=out_of_box)  # SRS suspect ?
    log.info("staging.swi_grille", path=str(out), n_mailles=n, hors_bbox=out_of_box)
    return out


def build_swi_maille(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    raw_dir: Path | None = None,
    out: Path | None = None,
) -> Path:
    """Construit `data/staging/swi_maille.parquet` (série mensuelle SWI par maille, **tout
    l'historique** — base de la climatologie).

    Lit les `swi.*.csv.gz` (en-tête `"NUMERO";"LAMBX";"LAMBY";"DATE";"SWI_UNIF_MENS"`,
    `delim=';'`). `DATE` = `AAAAMM` → 1er du mois. `SWI` brut (non clampé, peut déborder de
    `[0,1]`). La géométrie vient de la grille (`build_swi_grille`), pas d'ici.
    """
    s = get_settings()
    raw_dir = raw_dir or s.source_raw_dir(SOURCE_SWI)
    files = sorted(raw_dir.glob("swi.*.csv.gz"))
    if not files:
        raise FileNotFoundError("CSV.gz SWI absents — lance d'abord `make fetch-swi`.")
    out = out or (s.staging_dir / "swi_maille.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    glob = f"{raw_dir}/swi.*.csv.gz"

    own = con is None
    con = con or duckdb_io.connect()
    try:
        con.execute(
            f"""
            COPY (
              SELECT CAST(NUMERO AS INTEGER)                         AS num_maille,
                     CAST(strptime(CAST(DATE AS VARCHAR), '%Y%m') AS DATE) AS date_mois,
                     CAST(SWI_UNIF_MENS AS DOUBLE)                   AS swi
              FROM read_csv('{glob}', delim = ';', header = true)
            ) TO '{out}' (FORMAT PARQUET);
            """
        )
        stats = con.execute(
            f"""SELECT count(*), count(DISTINCT num_maille),
                       min(date_mois), max(date_mois)
                FROM read_parquet('{out}')"""
        ).fetchone()
        n, n_mailles, dmin, dmax = stats if stats else (0, 0, None, None)
    finally:
        if own:
            con.close()
    log.info(
        "staging.swi_maille",
        path=str(out),
        n_lignes=n,
        n_mailles=n_mailles,
        date_min=str(dmin),
        date_max=str(dmax),
        n_fichiers=len(files),
    )
    return out


def build_swi_clim(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    maille_parquet: Path | None = None,
    out: Path | None = None,
    ref_from: str | None = None,
    ref_to: str | None = None,
) -> Path:
    """Construit `data/staging/swi_clim.parquet` : climatologie **par maille et par mois
    calendaire** (moyenne, écart-type, n) sur l'historique.

    Par défaut tout l'historique (`ref_from`/`ref_to` None) ; passer une fenêtre (ex.
    `'1991-01-01'`/`'2020-12-01'`) pour une normale glissante. L'anomalie standardisée
    `z_SWI` est ensuite `(swi − swi_mean)/swi_std` pour le **même mois** (cf. `build_swi_anomalie`).
    """
    s = get_settings()
    maille_parquet = maille_parquet or (s.staging_dir / "swi_maille.parquet")
    out = out or (s.staging_dir / "swi_clim.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    bounds = []
    if ref_from:
        bounds.append(f"date_mois >= DATE '{ref_from}'")
    if ref_to:
        bounds.append(f"date_mois <= DATE '{ref_to}'")
    where = ("WHERE " + " AND ".join(bounds)) if bounds else ""

    own = con is None
    con = con or duckdb_io.connect()
    try:
        con.execute(
            f"""
            COPY (
              SELECT num_maille,
                     month(date_mois)::INTEGER AS mois_cal,
                     avg(swi)                   AS swi_mean,
                     stddev_samp(swi)           AS swi_std,
                     count(*)                   AS n
              FROM read_parquet('{maille_parquet}')
              {where}
              GROUP BY num_maille, month(date_mois)
            ) TO '{out}' (FORMAT PARQUET);
            """
        )
        stats = con.execute(
            f"""SELECT count(*), min(n), max(n),
                       count(*) FILTER (WHERE swi_std IS NULL OR swi_std < 1e-9)
                FROM read_parquet('{out}')"""
        ).fetchone()
        n, nmin, nmax, n_degenere = stats if stats else (0, 0, 0, 0)
    finally:
        if own:
            con.close()
    log.info(
        "staging.swi_clim",
        path=str(out),
        n_lignes=n,
        n_par_mois_min=nmin,
        n_par_mois_max=nmax,
        n_std_degenere=n_degenere,
    )
    return out


def build_swi_anomalie(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    maille_parquet: Path | None = None,
    clim_parquet: Path | None = None,
    out: Path | None = None,
    served_from: str = SWI_SERVED_FROM,
) -> Path:
    """Construit `data/staging/swi_anomalie.parquet` : anomalie standardisée `z_SWI` par
    maille et par mois, sur la **fenêtre servie** (`served_from` →, défaut 2017-01).

    `z_SWI = (swi − swi_mean)/swi_std` vs la climatologie du **même mois calendaire**.
    Sécheresse ⇒ `z_SWI` négatif. `swi_std` nul/dégénéré ⇒ `z_SWI` NULL (flag implicite :
    la commune retombera sur ses autres mailles / signalé en aval).
    """
    s = get_settings()
    maille_parquet = maille_parquet or (s.staging_dir / "swi_maille.parquet")
    clim_parquet = clim_parquet or (s.staging_dir / "swi_clim.parquet")
    out = out or (s.staging_dir / "swi_anomalie.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)

    own = con is None
    con = con or duckdb_io.connect()
    try:
        con.execute(
            f"""
            COPY (
              SELECT m.num_maille,
                     m.date_mois,
                     m.swi,
                     CASE WHEN c.swi_std IS NULL OR c.swi_std < 1e-9 THEN NULL
                          ELSE (m.swi - c.swi_mean) / c.swi_std END AS z_swi
              FROM read_parquet('{maille_parquet}') m
              JOIN read_parquet('{clim_parquet}') c
                ON c.num_maille = m.num_maille AND c.mois_cal = month(m.date_mois)
              WHERE m.date_mois >= DATE '{served_from}'
            ) TO '{out}' (FORMAT PARQUET);
            """
        )
        stats = con.execute(
            f"""SELECT count(*), count(*) FILTER (WHERE z_swi IS NULL),
                       round(avg(z_swi), 4), round(stddev_samp(z_swi), 4)
                FROM read_parquet('{out}')"""
        ).fetchone()
        n, n_null, zmean, zstd = stats if stats else (0, 0, None, None)
    finally:
        if own:
            con.close()
    log.info(
        "staging.swi_anomalie",
        path=str(out),
        n_lignes=n,
        n_z_null=n_null,
        z_moyen=zmean,
        z_ecart_type=zstd,
    )
    return out


def build_catnat_secheresse(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    raw_csv: Path | None = None,
    commune_parquet: Path | None = None,
    out: Path | None = None,
    departements: list[str] | None = None,
) -> Path:
    """Construit `data/staging/catnat_secheresse.parquet` : arrêtés Cat-Nat **sécheresse**
    agrégés **par commune** (fréquence, premier/dernier arrêté, années, évènements).

    Lit `catnat_gaspar.csv` (`;`, UTF-8, dates ISO), **filtre `lib_risque_jo='Sécheresse'`**
    (insensible casse/accents par robustesse), **déduplique** (commune × arrêté) sur
    `cod_nat_catnat` (un arrêté couvre N communes + correctifs), borne aux départements
    configurés (dept dérivé de l'INSEE), et **trace** le taux de communes orphelines vs le
    COG courant (`commune.parquet` ; millésimes COG distincts — cf. ADR-014). INSEE en
    **texte** (zéros, Corse 2A/2B).

    `catnat_gaspar.csv` ne liste que des **reconnaissances** (positifs) → substrat de
    calibration de `H` (M-B), pas une probabilité de reconnaissance (cf. `docs/metric.md §H`).
    """
    s = get_settings()
    raw_csv = raw_csv or (s.source_raw_dir(SOURCE_GASPAR) / "catnat_gaspar.csv")
    if not raw_csv.exists():
        raise FileNotFoundError("CSV GASPAR absent — lance d'abord `make fetch-gaspar`.")
    commune_parquet = commune_parquet or (s.staging_dir / "commune.parquet")
    out = out or (s.staging_dir / "catnat_secheresse.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    deps = s.departements if departements is None else departements
    dept_filter = ""
    if deps:
        lst = ", ".join(f"'{d}'" for d in deps)
        dept_filter = f"WHERE ({_DEPT_FROM_INSEE}) IN ({lst})"

    own = con is None
    con = con or duckdb_io.connect()
    try:
        # Lecture unique du CSV → table temp (sécheresse seule, INSEE/dates typés). Le
        # bornage département (sur `code_insee` dérivé) s'applique ensuite à l'agrégat.
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE _catnat_src AS
            SELECT cod_commune::VARCHAR                       AS code_insee,
                   cod_nat_catnat::VARCHAR                    AS cod_nat_catnat,
                   num_risque_jo::VARCHAR                     AS num_risque_jo,
                   TRY_CAST(dat_deb AS TIMESTAMP)::DATE        AS dat_deb,
                   TRY_CAST(dat_fin AS TIMESTAMP)::DATE        AS dat_fin,
                   TRY_CAST(dat_pub_arrete AS TIMESTAMP)::DATE AS dat_pub_arrete
            FROM read_csv('{raw_csv}', delim = ';', header = true, all_varchar = true)
            WHERE lower(strip_accents(lib_risque_jo)) = 'secheresse';
            """
        )
        con.execute(
            f"""
            COPY (
              WITH dedup AS (   -- 1 ligne / (commune, arrêté) : arrêté multi-communes + correctifs
                SELECT code_insee, cod_nat_catnat,
                       min(dat_deb)        AS dat_deb,
                       max(dat_fin)        AS dat_fin,
                       max(dat_pub_arrete) AS dat_pub_arrete
                FROM _catnat_src
                {dept_filter}
                GROUP BY code_insee, cod_nat_catnat
              )
              SELECT
                code_insee,
                count(*)            AS catnat_freq,
                min(dat_pub_arrete) AS premier_arrete,
                max(dat_pub_arrete) AS dernier_arrete,
                list_sort(list_distinct(list(year(dat_pub_arrete)))) AS annees_reco,
                list(struct_pack(
                       cod_nat_catnat := cod_nat_catnat,
                       dat_deb        := dat_deb,
                       dat_fin        := dat_fin,
                       annee          := year(dat_pub_arrete)))      AS evenements
              FROM dedup
              GROUP BY code_insee
            ) TO '{out}' (FORMAT PARQUET);
            """
        )
        stats = con.execute(
            f"""SELECT count(*), sum(catnat_freq), min(premier_arrete), max(dernier_arrete)
                FROM read_parquet('{out}')"""
        ).fetchone()
        n_comm, n_arretes, dmin, dmax = stats if stats else (0, None, None, None)
        # Self-check : un seul `num_risque_jo` doit co-occurrer avec 'Sécheresse' (national).
        n_codes = duckdb_io.scalar(con, "SELECT count(DISTINCT num_risque_jo) FROM _catnat_src")
        # Anti-jointure COG : communes d'arrêtés absentes du COG courant (millésime).
        n_orphelins = (
            duckdb_io.scalar(
                con,
                f"""SELECT count(*) FROM read_parquet('{out}') a
                    WHERE a.code_insee NOT IN (
                      SELECT code_insee FROM read_parquet('{commune_parquet}'))""",
            )
            if commune_parquet.exists()
            else None
        )
    finally:
        if own:
            con.close()
    if n_codes is not None and n_codes != 1:
        log.warning("staging.catnat_secheresse.num_risque_ambigu", n_codes=n_codes)
    log.info(
        "staging.catnat_secheresse",
        path=str(out),
        n_communes=n_comm,
        n_arretes=n_arretes,
        date_min=str(dmin),
        date_max=str(dmax),
        n_orphelins_cog=n_orphelins,
        bornage=deps or "national",
    )
    return out
