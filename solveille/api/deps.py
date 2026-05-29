"""Accès lecture seule au mart `commune_pression` pour l'API (DuckDB, attributs only).

Le mart ne porte pas de géométrie → pas besoin de l'extension spatial : connexion DuckDB
in-memory légère, requêtes paramétrées (anti-injection sur le code INSEE).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from solveille.common.config import get_settings


class MartUnavailableError(RuntimeError):
    """Le mart n'a pas encore été construit (`make build`)."""


def mart_path() -> Path:
    """Chemin du mart servi."""
    return get_settings().marts_dir / "commune_pression.parquet"


def _require_mart() -> Path:
    p = mart_path()
    if not p.exists():
        raise MartUnavailableError(f"Mart absent : {p} — lance `make build`.")
    return p


def fetch_commune(insee: str) -> dict[str, Any] | None:
    """Fiche d'une commune (toutes les colonnes du mart), ou None si inconnue."""
    p = _require_mart()
    con = duckdb.connect()
    try:
        cols = [c[0] for c in con.execute(f"SELECT * FROM read_parquet('{p}') LIMIT 0").description]
        row = con.execute(f"SELECT * FROM read_parquet('{p}') WHERE insee = ?", [insee]).fetchone()
        return dict(zip(cols, row, strict=True)) if row else None
    finally:
        con.close()


def fetch_meta() -> dict[str, Any]:
    """Dates de fraîcheur par source (`last_updated_*`) + volumétrie servie."""
    p = _require_mart()
    con = duckdb.connect()
    try:
        row = con.execute(
            f"""
            SELECT count(*)                                   AS n_communes,
                   count(*) FILTER (WHERE E > 0)              AS n_exposees,
                   count(*) FILTER (WHERE basculement_2026)   AS n_reclassees_2026,
                   count(*) FILTER (WHERE valeur_bati_exposee_eur IS NOT NULL) AS n_avec_valeur,
                   round(sum(valeur_bati_exposee_eur))        AS valeur_bati_exposee_totale_eur,
                   any_value(last_updated_admin_express)      AS last_updated_admin_express,
                   any_value(last_updated_rga)                AS last_updated_rga,
                   any_value(last_updated_bascule)            AS last_updated_bascule,
                   any_value(last_updated_insee)              AS last_updated_insee,
                   any_value(last_updated_fideli)             AS last_updated_fideli,
                   any_value(last_updated_dvf)                AS last_updated_dvf
            FROM read_parquet('{p}')
            """
        ).fetchone()
        cols = [
            "n_communes",
            "n_exposees",
            "n_reclassees_2026",
            "n_avec_valeur",
            "valeur_bati_exposee_totale_eur",
            "last_updated_admin_express",
            "last_updated_rga",
            "last_updated_bascule",
            "last_updated_insee",
            "last_updated_fideli",
            "last_updated_dvf",
        ]
        return dict(zip(cols, row, strict=True)) if row else {}
    finally:
        con.close()
