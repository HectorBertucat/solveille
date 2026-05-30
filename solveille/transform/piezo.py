"""Staging Hub'eau piézométrie : raw NDJSON → Parquet (EPSG:2154, médianes mensuelles NGF).

Deux tables (base de l'IPS, cf. `piezo_ips.py`) :
1. **`piezo_stations`** : référentiel des stations gardées (≥ 15 ans), point reprojeté en
   **EPSG:2154** (coords API en WGS84, `lon=x`/`lat=y` → `always_xy:=true`), avec `code_bss`,
   `code_commune_insee` (contrôle), dates, `codes_bdlisa` (libre/captive en M2), span en années.
2. **`piezo_mensuel`** : **médiane mensuelle** du niveau NGF (`niveau_nappe_eau`) par
   `(code_bss, mois)` — base de la climatologie (un point par station × année × mois calendaire).

NGF haut = nappe haute = humide (cf. connecteur). `chroniques_tr` non utilisé ici.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from solveille.common import duckdb_io
from solveille.common.config import get_settings
from solveille.common.geo import METROPOLE_L93_BBOX
from solveille.common.logging import get_logger

log = get_logger("solveille.transform.piezo")

SOURCE = "hubeau_piezo"
#: Historique minimal (années) — réplique `ingest.hubeau_piezo.MIN_YEARS` (gating IPS).
MIN_YEARS = 15

# Stations : NDJSON → point WGS84 (lon=x, lat=y) reprojeté en 2154 (always_xy obligatoire,
# comme RGA). On garde le span (années) et les codes BDLISA (texte) pour la confiance.
_STATIONS_SQL = """
COPY (
  SELECT
    code_bss::VARCHAR                                   AS code_bss,
    NULLIF(CAST(code_commune_insee AS VARCHAR), '')     AS code_commune_insee,
    CAST(code_departement AS VARCHAR)                   AS code_departement,
    CAST(date_debut_mesure AS DATE)                     AS date_debut,
    CAST(date_fin_mesure AS DATE)                       AS date_fin,
    (CAST(date_fin_mesure AS DATE) - CAST(date_debut_mesure AS DATE)) / 365.25 AS span_annees,
    TRY_CAST(nb_mesures_piezo AS BIGINT)                AS nb_mesures_piezo,
    TRY_CAST(profondeur_investigation AS DOUBLE)        AS profondeur_investigation,
    CAST(codes_bdlisa AS VARCHAR)                       AS codes_bdlisa,
    ST_AsWKB(ST_Transform(ST_Point(CAST(x AS DOUBLE), CAST(y AS DOUBLE)),
             'EPSG:4326', 'EPSG:2154', always_xy := true)) AS geom_wkb
  FROM read_json('{glob}', format = 'newline_delimited', union_by_name = true)
  WHERE x IS NOT NULL AND y IS NOT NULL AND code_bss IS NOT NULL
) TO '{out}' (FORMAT PARQUET);
"""

# Borne mémoire des builds piézo nationaux (VM 8 Go **partagée**) : DuckDB spille au disque
# plutôt que de se faire tuer par l'OOM killer. Le national = 18 M+ mesures.
PIEZO_MEMORY_LIMIT = "4GB"
# Lecture des chroniques **par lots** de fichiers (anti-OOM) : `read_json` sur 2809 fichiers
# d'un coup fait exploser la RAM. 1 station = 1 fichier ⇒ chaque (code_bss, mois) est complet
# dans son lot ⇒ la médiane par lot est exacte (pas de fusion inter-lots nécessaire).
MENSUEL_BATCH = 200

_MENSUEL_DDL = """
CREATE OR REPLACE TEMP TABLE _piezo_mensuel (
  code_bss VARCHAR, date_mois DATE, ngf DOUBLE, n_obs BIGINT
)
"""

# Médiane mensuelle du NGF (robuste aux pics) par (code_bss, mois), pour UN lot de fichiers.
_MENSUEL_INSERT_SQL = """
INSERT INTO _piezo_mensuel
SELECT code_bss::VARCHAR,
       CAST(date_trunc('month', CAST(date_mesure AS DATE)) AS DATE),
       median(TRY_CAST(niveau_nappe_eau AS DOUBLE)),
       count(*)
FROM read_json([{files}], format = 'newline_delimited', union_by_name = true)
WHERE niveau_nappe_eau IS NOT NULL AND date_mesure IS NOT NULL
GROUP BY code_bss, date_trunc('month', CAST(date_mesure AS DATE))
"""


def build_piezo_stations(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    raw_dir: Path | None = None,
    out: Path | None = None,
) -> Path:
    """Construit `data/staging/piezo_stations.parquet` (stations reprojetées 2154).

    Garde-fou SRS : alerte si un point reprojeté sort de l'emprise métropole (reproj suspecte).
    """
    s = get_settings()
    raw_dir = raw_dir or (s.source_raw_dir(SOURCE) / "stations")
    out = out or (s.staging_dir / "piezo_stations.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    if not sorted(raw_dir.glob("*.jsonl")):
        raise FileNotFoundError("Stations piézo absentes — lance d'abord `make fetch-piezo`.")
    xmin, ymin, xmax, ymax = METROPOLE_L93_BBOX

    own = con is None
    con = con or duckdb_io.connect(memory_limit=PIEZO_MEMORY_LIMIT)
    try:
        con.execute(_STATIONS_SQL.format(glob=f"{raw_dir}/*.jsonl", out=out))
        n = duckdb_io.scalar(con, f"SELECT count(*) FROM read_parquet('{out}')")
        out_of_box = duckdb_io.scalar(
            con,
            f"""SELECT count(*) FROM read_parquet('{out}')
                WHERE ST_X(ST_GeomFromWKB(geom_wkb)) NOT BETWEEN {xmin} AND {xmax}
                   OR ST_Y(ST_GeomFromWKB(geom_wkb)) NOT BETWEEN {ymin} AND {ymax}""",
        )
        n_short = duckdb_io.scalar(
            con, f"SELECT count(*) FROM read_parquet('{out}') WHERE span_annees < {MIN_YEARS}"
        )
    finally:
        if own:
            con.close()
    if out_of_box:
        log.warning("piezo_stations.out_of_metropole_bbox", n=out_of_box)  # reproj suspecte ?
    log.info(
        "staging.piezo_stations",
        path=str(out),
        n_stations=n,
        hors_bbox=out_of_box,
        n_span_court=n_short,
    )
    return out


def build_piezo_mensuel(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    raw_dir: Path | None = None,
    out: Path | None = None,
) -> Path:
    """Construit `data/staging/piezo_mensuel.parquet` (médiane mensuelle NGF par station)."""
    s = get_settings()
    raw_dir = raw_dir or (s.source_raw_dir(SOURCE) / "chroniques")
    out = out or (s.staging_dir / "piezo_mensuel.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(raw_dir.glob("*.jsonl"))
    if not files:
        raise FileNotFoundError("Chroniques piézo absentes — lance d'abord `make fetch-piezo`.")

    own = con is None
    con = con or duckdb_io.connect(memory_limit=PIEZO_MEMORY_LIMIT)
    try:
        con.execute(_MENSUEL_DDL)
        for i in range(0, len(files), MENSUEL_BATCH):  # anti-OOM : par lots de fichiers
            flist = ", ".join(f"'{f}'" for f in files[i : i + MENSUEL_BATCH])
            con.execute(_MENSUEL_INSERT_SQL.format(files=flist))
        con.execute(f"COPY _piezo_mensuel TO '{out}' (FORMAT PARQUET);")
        stats = con.execute(
            f"""SELECT count(*), count(DISTINCT code_bss),
                       min(date_mois), max(date_mois)
                FROM read_parquet('{out}')"""
        ).fetchone()
        n, n_bss, dmin, dmax = stats if stats else (0, 0, None, None)
    finally:
        if own:
            con.close()
    log.info(
        "staging.piezo_mensuel",
        path=str(out),
        n_lignes=n,
        n_stations=n_bss,
        date_min=str(dmin),
        date_max=str(dmax),
    )
    return out
