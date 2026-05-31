"""Accès lecture seule aux marts pour l'API (DuckDB, attributs only).

`commune_pression` (statique : E, J, flags, dernier mois) + `commune_pression_mensuel`
(série T/score/niveau par mois). Les marts ne portent pas de géométrie → pas besoin de
l'extension spatial : connexion DuckDB in-memory légère, requêtes paramétrées
(anti-injection sur le code INSEE et le mois).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb

from solveille.common.config import get_settings

#: Colonnes mensuelles fusionnées dans la fiche commune (dynamique du mois servi).
_MENSUEL_COLS = (
    "z_swi",
    "dry_swi",
    "z_ips",
    "dry_ips",
    "ips_classe",
    "T",
    "confiance_t",
    "ip_rga_score",
    "ip_rga_niveau",
    "ip_rga_niveau_code",
    "h_proba",
    "h_n_events",
    "h_pool_level",
)


class MartUnavailableError(RuntimeError):
    """Le mart n'a pas encore été construit (`make build`)."""


def mart_path() -> Path:
    """Chemin du mart statique servi."""
    return get_settings().marts_dir / "commune_pression.parquet"


def mensuel_path() -> Path:
    """Chemin du mart mensuel (série temporelle)."""
    return get_settings().marts_dir / "commune_pression_mensuel.parquet"


def seuils_path() -> Path:
    """Chemin du JSON des seuils de niveaux."""
    return get_settings().marts_dir / "seuils_niveaux.json"


def _require_mart() -> Path:
    p = mart_path()
    if not p.exists():
        raise MartUnavailableError(f"Mart absent : {p} — lance `make build`.")
    return p


def fetch_commune(insee: str, mois: str | None = None) -> dict[str, Any] | None:
    """Fiche d'une commune (colonnes statiques + dynamique du mois servi), ou None si inconnue.

    `mois` (`'AAAA-MM'`) sélectionne le mois affiché ; défaut = dernier mois disponible. La
    fiche fusionne alors `date`, `ip_rga_score`/`ip_rga_niveau` et `z_swi`/`dry_swi`/`T`/
    `confiance_t` de ce mois (sinon ceux du dernier mois déjà portés par le mart statique).
    """
    p = _require_mart()
    con = duckdb.connect()
    try:
        cols = [c[0] for c in con.execute(f"SELECT * FROM read_parquet('{p}') LIMIT 0").description]
        row = con.execute(f"SELECT * FROM read_parquet('{p}') WHERE insee = ?", [insee]).fetchone()
        if row is None:
            return None
        out = dict(zip(cols, row, strict=True))
        # `dernier_arrete` (GASPAR) est une DATE DuckDB → ISO string pour la sérialisation JSON.
        if out.get("dernier_arrete") is not None:
            out["dernier_arrete"] = str(out["dernier_arrete"])

        mp = mensuel_path()
        if mp.exists():
            # Tolérant au schéma : on ne sélectionne que les colonnes présentes (le code peut
            # être déployé avant que le mart soit reconstruit avec une nouvelle colonne IPS).
            head = con.execute(f"SELECT * FROM read_parquet('{mp}') LIMIT 0").description
            present = {c[0] for c in head}
            mcols = [c for c in _MENSUEL_COLS if c in present]
            sel = "date_mois, " + ", ".join(mcols)
            if mois:
                mrow = con.execute(
                    f"SELECT {sel} FROM read_parquet('{mp}') "
                    "WHERE insee = ? AND date_mois = CAST(? AS DATE)",
                    [insee, f"{mois}-01"],
                ).fetchone()
            else:
                mrow = con.execute(
                    f"SELECT {sel} FROM read_parquet('{mp}') "
                    "WHERE insee = ? ORDER BY date_mois DESC LIMIT 1",
                    [insee],
                ).fetchone()
            if mrow:
                out["date"] = str(mrow[0])
                out.update(dict(zip(mcols, mrow[1:], strict=True)))
        return out
    finally:
        con.close()


def fetch_serie(insee: str) -> list[dict[str, Any]]:
    """Série mensuelle (T, score, niveau) d'une commune, ordonnée par date (sparkline)."""
    mp = mensuel_path()
    if not mp.exists():
        return []
    con = duckdb.connect()
    try:
        rows = con.execute(
            "SELECT date_mois::VARCHAR, T, ip_rga_score, ip_rga_niveau, ip_rga_niveau_code "
            f"FROM read_parquet('{mp}') WHERE insee = ? ORDER BY date_mois",
            [insee],
        ).fetchall()
    finally:
        con.close()
    return [
        {"date_mois": d, "T": t, "ip_rga_score": s, "ip_rga_niveau": n, "ip_rga_niveau_code": c}
        for d, t, s, n, c in rows
    ]


def _seuils() -> dict[str, Any] | None:
    p = seuils_path()
    if p.exists():
        data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
        return data
    return None


def fetch_meta() -> dict[str, Any]:
    """Dates de fraîcheur par source (`last_updated_*`) + volumétrie + plage temporelle + seuils."""
    p = _require_mart()
    con = duckdb.connect()
    try:
        # Tolérant au schéma : `last_updated_ips` absent si le mart n'a pas encore été reconstruit
        # avec le code v1.1 (déploiement code/données dissocié).
        cols0 = {
            c[0] for c in con.execute(f"SELECT * FROM read_parquet('{p}') LIMIT 0").description
        }
        lu_ips = "any_value(last_updated_ips)" if "last_updated_ips" in cols0 else "NULL"
        # Tolérant au schéma : `last_updated_gaspar` absent si le mart n'a pas été reconstruit
        # avec le code v2 (déploiement code/données dissocié — sinon 500 en prod).
        lu_gaspar = "any_value(last_updated_gaspar)" if "last_updated_gaspar" in cols0 else "NULL"
        row = con.execute(
            f"""
            SELECT count(*)                                   AS n_communes,
                   count(*) FILTER (WHERE E > 0)              AS n_exposees,
                   count(*) FILTER (WHERE basculement_2026)   AS n_reclassees_2026,
                   count(*) FILTER (WHERE valeur_bati_exposee_eur IS NOT NULL) AS n_avec_valeur,
                   round(sum(valeur_bati_exposee_eur))        AS valeur_bati_exposee_totale_eur,
                   any_value(date)                            AS dernier_mois,
                   any_value(last_updated_admin_express)      AS last_updated_admin_express,
                   any_value(last_updated_rga)                AS last_updated_rga,
                   any_value(last_updated_bascule)            AS last_updated_bascule,
                   any_value(last_updated_insee)              AS last_updated_insee,
                   any_value(last_updated_fideli)             AS last_updated_fideli,
                   any_value(last_updated_dvf)                AS last_updated_dvf,
                   any_value(last_updated_swi)                AS last_updated_swi,
                   {lu_ips}                                   AS last_updated_ips,
                   {lu_gaspar}                                AS last_updated_gaspar
            FROM read_parquet('{p}')
            """
        ).fetchone()
        cols = [
            "n_communes",
            "n_exposees",
            "n_reclassees_2026",
            "n_avec_valeur",
            "valeur_bati_exposee_totale_eur",
            "dernier_mois",
            "last_updated_admin_express",
            "last_updated_rga",
            "last_updated_bascule",
            "last_updated_insee",
            "last_updated_fideli",
            "last_updated_dvf",
            "last_updated_swi",
            "last_updated_ips",
            "last_updated_gaspar",
        ]
        meta = dict(zip(cols, row, strict=True)) if row else {}
        meta["dernier_mois"] = str(meta["dernier_mois"]) if meta.get("dernier_mois") else None

        mp = mensuel_path()
        if mp.exists():
            mn = con.execute(
                "SELECT min(date_mois)::VARCHAR, max(date_mois)::VARCHAR, "
                f"count(DISTINCT date_mois) FROM read_parquet('{mp}')"
            ).fetchone()
            if mn:
                meta["mois_disponibles"] = {"min": mn[0], "max": mn[1], "n": mn[2]}
        seuils = _seuils()
        if seuils:
            meta["seuils_niveaux"] = seuils
        return meta
    finally:
        con.close()
