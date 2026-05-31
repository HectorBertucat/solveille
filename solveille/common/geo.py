"""Constantes et garde-fous géographiques (SRS de référence : EPSG:2154)."""

from __future__ import annotations

#: SRS cible du projet (Lambert 93 / RGF93) — tout est reprojeté ici dès l'ingestion.
L93 = "EPSG:2154"
L93_SRID = 2154
#: SRS d'arrivée de DVF (centroïdes parcelle) et de la livraison France entière IGN.
WGS84 = "EPSG:4326"

#: Enveloppe approximative France métropolitaine + Corse en Lambert 93 (mètres),
#: avec marge. Sert aux contrôles de cohérence SRS : un point hors de cette boîte
#: signale une géométrie non reprojetée (xmin, ymin, xmax, ymax).
METROPOLE_L93_BBOX: tuple[float, float, float, float] = (
    50_000.0,
    6_000_000.0,
    1_310_000.0,
    7_150_000.0,
)


def dept_expr_from_insee(col: str = "code_insee") -> str:
    """Expression SQL DuckDB dérivant le **code département** d'un code INSEE commune `col`.

    Métropole = 2 premiers caractères ; **Corse 2A/2B** (codes INSEE `2A…`/`2B…`) ; **DROM**
    `97x`/`98x` = 3 premiers caractères. Source **unique** partagée (staging GASPAR, calibration
    `H`) du bornage/poolage par département.
    """
    return (
        f"CASE WHEN substr({col}, 1, 2) IN ('2A', '2B') THEN substr({col}, 1, 2) "
        f"WHEN substr({col}, 1, 2) IN ('97', '98') THEN substr({col}, 1, 3) "
        f"ELSE substr({col}, 1, 2) END"
    )
