"""Index de recherche communal statique (`front/communes-index.json`) → recherche fuzzy MiniSearch.

Joint la géométrie commune (`commune.parquet` : nom COG accentué, département, **bbox WGS84**) aux
**codes postaux** (base La Poste, optionnelle) → un index colonnaire léger (~0,5 Mo gzip) servi au
front. La recherche actuelle (`querySourceFeatures` : seulement les communes rendues + match exact)
est remplacée par un index complet des 34 746 communes (nom, accents repliés, CP, code INSEE).

Pièges traités :
- **PLM** : la base CP utilise les codes INSEE d'**arrondissement** (75101-75120, 69381-69389,
  13201-13216), pas le code COG commune (75056/69123/13055) que la carte affiche → on **roule** les
  CP d'arrondissement vers la commune parent (sinon Paris/Lyon/Marseille seraient sans CP).
- **Encodage** : le CSV La Poste est en **Latin-1** (pas UTF-8) ; en-tête préfixée d'un `#`
  (neutralisée par `comment=''`). INSEE en **texte** (Corse 2A/2B, zéros de tête).
- CP **optionnel** : sans `make fetch-cp`, l'index se construit quand même (recherche par nom/INSEE,
  `cp` vide) — comme les chaînes IPS/H.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

from solveille.common import duckdb_io
from solveille.common.config import get_settings
from solveille.common.logging import get_logger
from solveille.common.raw import read_manifest

log = get_logger("solveille.transform.build_search")

SOURCE_CP = "laposte_codes_postaux"
#: Sortie servie par le front (chemin fixe : revalidé via ETag, cf. deploy/Caddyfile).
_FRONT_DIR = Path(__file__).resolve().parents[2] / "front"

# Rollup des arrondissements PLM vers la commune COG (la base CP n'a que les arrondissements).
_PLM_ROLLUP = """CASE
    WHEN code_insee BETWEEN '75101' AND '75120' THEN '75056'
    WHEN code_insee BETWEEN '69381' AND '69389' THEN '69123'
    WHEN code_insee BETWEEN '13201' AND '13216' THEN '13055'
    ELSE code_insee END"""


def _cp_cte(cp_csv: Path) -> str:
    """CTE `cp_agg(code_insee, cp[])` à partir du CSV La Poste (Latin-1, `;`, en-tête `#`)."""
    read = (
        f"read_csv('{cp_csv}', delim=';', header=true, comment='', all_varchar=true, "
        "encoding='latin-1', "
        "names=['code_insee','nom_postal','code_postal','libelle','ligne_5'])"
    )
    return f"""
    cp_norm AS (
      SELECT {_PLM_ROLLUP} AS code_insee, code_postal
      FROM {read} WHERE code_postal IS NOT NULL AND code_postal <> ''
    ),
    cp_agg AS (
      SELECT code_insee, list(DISTINCT code_postal ORDER BY code_postal) AS cp
      FROM cp_norm GROUP BY code_insee
    )"""


def build_communes_index(
    con: duckdb.DuckDBPyConnection | None = None,
    *,
    commune_parquet: Path | None = None,
    cp_csv: Path | None = None,
    mensuel_parquet: Path | None = None,
    out: Path | None = None,
) -> Path:
    """Écrit `front/communes-index.json` (format colonnaire) : nom, département, bbox WGS84, codes
    postaux et niveau du dernier mois (pastille des suggestions) par commune. Renvoie le chemin.
    CP **et** mart mensuel optionnels (skip propre si absents)."""
    s = get_settings()
    commune_parquet = commune_parquet or (s.staging_dir / "commune.parquet")
    cp_csv = cp_csv or (s.source_raw_dir(SOURCE_CP) / "codes_postaux.csv")
    mensuel_parquet = mensuel_parquet or (s.marts_dir / "commune_pression_mensuel.parquet")
    out = out or (_FRONT_DIR / "communes-index.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    own = con is None
    con = con or duckdb_io.connect()
    try:
        has_cp = cp_csv.exists()
        if not has_cp:
            log.warning("search.cp_absent", path=str(cp_csv))  # recherche par nom/INSEE seulement
        has_niv = mensuel_parquet.exists()
        cte = _cp_cte(cp_csv) + "," if has_cp else ""
        cp_join = "LEFT JOIN cp_agg a ON a.code_insee = g.code_insee" if has_cp else ""
        cp_sel = "COALESCE(a.cp, []::VARCHAR[])" if has_cp else "[]::VARCHAR[]"
        # Niveau IP-RGA du **dernier mois** (= la carte par défaut) → pastille des suggestions.
        niv_cte = (
            f""", niv AS (
              SELECT insee, arg_max(ip_rga_niveau_code, date_mois) AS niveau
              FROM read_parquet('{mensuel_parquet}') GROUP BY insee
            )"""
            if has_niv
            else ""
        )
        niv_join = "LEFT JOIN niv ON niv.insee = g.code_insee" if has_niv else ""
        niv_sel = "COALESCE(niv.niveau, 0)" if has_niv else "0"
        rows = con.execute(
            f"""
            WITH {cte}
            g AS (
              SELECT code_insee, nom, code_dept,
                ST_Transform(ST_GeomFromWKB(geom_wkb), 'EPSG:2154', 'EPSG:4326', always_xy := true)
                  AS geom
              FROM read_parquet('{commune_parquet}')
            ){niv_cte}
            SELECT g.code_insee, g.nom, g.code_dept,
                   round(ST_XMin(g.geom), 4) AS minx, round(ST_YMin(g.geom), 4) AS miny,
                   round(ST_XMax(g.geom), 4) AS maxx, round(ST_YMax(g.geom), 4) AS maxy,
                   {cp_sel} AS cp, {niv_sel} AS niveau
            FROM g {cp_join} {niv_join}
            ORDER BY g.code_insee
            """
        ).fetchall()
    finally:
        if own:
            con.close()

    # Format colonnaire (gzippe bien) : 1 tableau par champ + bbox [minx,miny,maxx,maxy].
    insee, nom, dept, bbox, cp, niveau = [], [], [], [], [], []
    for r in rows:
        insee.append(r[0])
        nom.append(r[1])
        dept.append(r[2])
        bbox.append([r[3], r[4], r[5], r[6]])
        cp.append(list(r[7]) if r[7] else [])
        niveau.append(int(r[8]) if r[8] is not None else 0)

    manifest = read_manifest(s.source_raw_dir(SOURCE_CP)) or {}
    payload = {
        "last_updated_cp": manifest.get("source_version") or manifest.get("date_fetch"),
        "n": len(insee),
        "fields": ["insee", "nom", "dept", "bbox", "cp", "niveau"],
        "data": {
            "insee": insee,
            "nom": nom,
            "dept": dept,
            "bbox": bbox,
            "cp": cp,
            "niveau": niveau,
        },
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    n_cp = sum(1 for c in cp if c)
    log.info("search.index", path=str(out), n=len(insee), n_avec_cp=n_cp, bytes=out.stat().st_size)
    return out


def main() -> None:
    build_communes_index()


if __name__ == "__main__":
    main()
