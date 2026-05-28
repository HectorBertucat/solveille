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
