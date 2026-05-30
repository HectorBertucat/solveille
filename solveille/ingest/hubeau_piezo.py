"""Connecteur Hub'eau Piézométrie (BRGM / ADES) — niveaux de nappe pour l'IPS (tension `T`).

Vérifié live (API v1.4.3, `data-source-researcher`, mai 2026). Pièges encodés ici (ADR-017,
`docs/data-sources.md §5`) :
- **`chroniques` IGNORE silencieusement tout filtre géographique** (`code_departement` /
  `code_commune` renvoient les ~25 M lignes nationales, sans erreur 400) → on **liste d'abord
  les `code_bss` via `stations?code_departement=`**, puis on boucle `chroniques?code_bss=…`
  (jamais de `chroniques` sans `code_bss`).
- Plafond dur **`page × size ≤ 20000`**, pas de curseur → on requête `size=20000, page=1` et,
  si `count > 20000`, on **fenêtre par dates** (découpe récursive de l'intervalle).
- Coordonnées `stations` en **WGS84 (lon=x, lat=y)** → reprojetées en 2154 au staging.
- Niveau = **`niveau_nappe_eau`** (cote NGF, m) ; `chroniques_tr` (champ `niveau_eau_ngf`,
  horaire, ~3 mois) n'est PAS utilisé ici : **toute la climatologie vient de `chroniques`**.

Connecteur **poli/idempotent** : séquentiel par station, pause ≥ `http_pause_s`, backoff
(via `common.http`), cache local (on saute une station dont la chronique couvre déjà son
`date_fin_mesure`). Borné par `SOLVEILLE_DEPARTEMENTS` (Occitanie → national, ADR-014).
Licence Ouverte 2.0 — ADES / BRGM (OFB).
"""

from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

from solveille.common import http
from solveille.common.config import SWI_SERVED_FROM, get_settings
from solveille.common.logging import get_logger
from solveille.common.raw import RawDataset, utc_now_iso, write_manifest

log = get_logger("solveille.ingest.hubeau_piezo")

SOURCE = "hubeau_piezo"

#: Pagination Hub'eau : plafond dur `page × size ≤ 20000` (size max par page).
PAGE_SIZE = 20000
#: Historique minimal exigé (méthode BRGM ; ≥ 15 ans acceptable, ≥ 30 idéal).
MIN_YEARS = 15
#: Profondeur **maximale** d'historique téléchargé par station (depuis `date_fin`). Borne le
#: volume — une station mesurée depuis 1899 ou en pas sous-horaire sur des décennies déclenche
#: sinon un fenêtrage récursif très lourd (≫ 20000 pts) et un fetch national interminable. 35 ans
#: garde la fenêtre BRGM ≥ 30 ans. Ancré sur `date_fin` (idempotent, indépendant de la date de run).
MAX_HISTORY_YEARS = 35
#: Pause minimale entre requêtes station (politesse réseau : ≤ ~2 req/s).
MIN_PAUSE_S = 0.5
#: Départements métropole (repli si `SOLVEILLE_DEPARTEMENTS` vide → run national, M3).
METROPOLE_DEPTS: tuple[str, ...] = (
    *(f"{d:02d}" for d in range(1, 20)),  # 01..19
    "2A",
    "2B",
    *(f"{d:02d}" for d in range(21, 96)),  # 21..95
)
#: Début de la fenêtre servie (une station doit avoir des mesures après cette date pour peser).
SERVED_FROM = date.fromisoformat(SWI_SERVED_FROM)

LICENCE = (
    "Niveaux piézométriques (cote NGF) de la banque ADES. Source : Hub'eau / ADES — "
    "BRGM (OFB), Licence Ouverte 2.0 (Etalab)."
)


def _endpoint(name: str) -> str:
    base = get_settings().hubeau_base.rstrip("/")
    return f"{base}/niveaux_nappes/{name}"


def _safe(code_bss: str) -> str:
    """`code_bss` → nom de fichier sûr (le code contient un `/`, ex. `09892X0679/EXH70`)."""
    return code_bss.replace("/", "_").replace(" ", "_")


def _years_between(d_start: str | None, d_end: str | None) -> float | None:
    """Durée en années (décimales) entre deux dates ISO `YYYY-MM-DD`, ou None si invalide."""
    try:
        a = date.fromisoformat((d_start or "")[:10])
        b = date.fromisoformat((d_end or "")[:10])
    except ValueError:
        return None
    return (b - a).days / 365.25


def _keep_station(st: dict[str, Any]) -> bool:
    """Garde une station si historique ≥ 15 ans **et** des mesures dans la fenêtre servie."""
    d_deb, d_fin = st.get("date_debut_mesure"), st.get("date_fin_mesure")
    span = _years_between(d_deb, d_fin)
    if span is None or span < MIN_YEARS:
        return False
    try:
        fin = date.fromisoformat(str(d_fin)[:10])
    except ValueError:
        return False
    return fin >= SERVED_FROM  # sinon aucune contribution à la fenêtre 2017→


def fetch_stations(
    dept: str, *, client: httpx.Client, root: Path
) -> list[dict[str, Any]]:
    """Liste les stations d'un département (1 requête), écrit le brut, renvoie celles à garder."""
    payload = http.get_json(
        _endpoint("stations"),
        params={"code_departement": dept, "format": "json", "size": PAGE_SIZE},
        client=client,
    )
    stations = payload.get("data", []) or []
    kept = [s for s in stations if _keep_station(s)]
    (root / "stations").mkdir(parents=True, exist_ok=True)
    # On n'écrit QUE les stations gardées (≥15 ans, mesures dans la fenêtre servie) → brut,
    # staging et chroniques cohérents. NDJSON : lecture DuckDB streaming, sans UNNEST.
    (root / "stations" / f"{dept}.jsonl").write_text(
        "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in kept), encoding="utf-8"
    )
    log.info("piezo.stations", dept=dept, n_total=len(stations), n_gardees=len(kept))
    return kept


def _fetch_window(
    code_bss: str, d_deb: date, d_fin: date, *, client: httpx.Client
) -> list[dict[str, Any]]:
    """Récupère les mesures `chroniques` d'une station sur `[d_deb, d_fin]`.

    Si `count > 20000`, **découpe l'intervalle en deux** (récursion par dates) — le seul moyen
    de dépasser le plafond dur de pagination (pas de curseur).
    """
    payload = http.get_json(
        _endpoint("chroniques"),
        params={
            "code_bss": code_bss,
            "date_debut_mesure": d_deb.isoformat(),
            "date_fin_mesure": d_fin.isoformat(),
            "size": PAGE_SIZE,
            "sort": "asc",
        },
        client=client,
    )
    count = int(payload.get("count") or 0)
    rows = payload.get("data", []) or []
    if count <= len(rows):
        return rows
    if d_deb >= d_fin:  # une seule journée déborde (impossible en piézo) → on garde la page
        log.warning("piezo.window_saturated", code_bss=code_bss, jour=d_deb.isoformat())
        return rows
    mid = d_deb + (d_fin - d_deb) // 2
    time.sleep(MIN_PAUSE_S)
    return _fetch_window(code_bss, d_deb, mid, client=client) + _fetch_window(
        code_bss, mid + timedelta(days=1), d_fin, client=client
    )


def _lines(code_bss: str, rows: list[dict[str, Any]]) -> str:
    """Sérialise les mesures en NDJSON (`code_bss` injecté sur chaque ligne)."""
    return "".join(json.dumps({"code_bss": code_bss, **r}, ensure_ascii=False) + "\n" for r in rows)


def _history_start(d_deb: date, d_fin: date) -> date:
    """Début de fetch borné à `MAX_HISTORY_YEARS` avant `date_fin` (volume/politesse).

    `max(date_debut, date_fin − 35 ans)` : ne tronque que les stations à très long historique
    (1899→…) ; garde toute la chronique des stations plus courtes. Ancré sur `date_fin` ⇒
    idempotent (indépendant de la date d'exécution).
    """
    floor = d_fin - timedelta(days=round(MAX_HISTORY_YEARS * 365.25))
    return max(d_deb, floor)


def fetch_chronique(
    st: dict[str, Any], *, client: httpx.Client, root: Path
) -> tuple[Path, int, str]:
    """Télécharge la chronique d'une station (idempotent, **incrémental**), écrit le brut.

    Politesse : si la chronique cachée couvre déjà `date_fin_mesure`, on saute ; si elle est
    en retard, on ne récupère que **l'incrément** (`[cov.date_fin+1, date_fin]`) qu'on **ajoute**
    au NDJSON (essentiel pour le refresh quotidien — pas de re-téléchargement de tout l'historique
    quand une station gagne un point). Renvoie (chemin, n_mesures, statut).
    """
    code_bss = st["code_bss"]
    d_fin = date.fromisoformat(st["date_fin_mesure"][:10])
    d_deb = _history_start(date.fromisoformat(st["date_debut_mesure"][:10]), d_fin)
    cdir = root / "chroniques"
    cdir.mkdir(parents=True, exist_ok=True)
    dest = cdir / f"{_safe(code_bss)}.jsonl"
    marker = dest.with_suffix(".cover.json")

    if dest.exists() and marker.exists():
        try:
            cov = json.loads(marker.read_text(encoding="utf-8"))
            cov_fin = date.fromisoformat(cov["date_fin"]) if cov.get("date_fin") else None
        except (json.JSONDecodeError, ValueError):
            cov, cov_fin = {}, None
        if cov_fin and cov_fin >= d_fin:  # rien de nouveau
            log.info("piezo.chronique_cached", code_bss=code_bss, n=cov.get("n", 0))
            return dest, int(cov.get("n") or 0), "cached"
        if cov_fin and cov_fin < d_fin:  # incrément seulement → on ajoute au NDJSON
            rows = _fetch_window(code_bss, cov_fin + timedelta(days=1), d_fin, client=client)
            with dest.open("a", encoding="utf-8") as fh:
                fh.write(_lines(code_bss, rows))
            new_n = int(cov.get("n") or 0) + len(rows)
            marker.write_text(
                json.dumps(
                    {
                        "date_debut": cov.get("date_debut", d_deb.isoformat()),
                        "date_fin": d_fin.isoformat(),
                        "n": new_n,
                    }
                )
            )
            log.info("piezo.chronique_incr", code_bss=code_bss, n_nouveaux=len(rows), n=new_n)
            return dest, new_n, "incremental"

    rows = _fetch_window(code_bss, d_deb, d_fin, client=client)
    dest.write_text(_lines(code_bss, rows), encoding="utf-8")
    marker.write_text(
        json.dumps({"date_debut": d_deb.isoformat(), "date_fin": d_fin.isoformat(), "n": len(rows)})
    )
    log.info("piezo.chronique", code_bss=code_bss, n=len(rows))
    return dest, len(rows), "downloaded"


def fetch() -> RawDataset:
    """Ingère stations + chroniques Hub'eau dans `data/raw/hubeau_piezo/`, borné par département."""
    s = get_settings()
    root = s.source_raw_dir(SOURCE)
    root.mkdir(parents=True, exist_ok=True)
    depts = s.departements or list(METROPOLE_DEPTS)
    pause = max(s.http_pause_s, MIN_PAUSE_S)

    files: list[Path] = []
    n_stations = n_chron = n_obs = 0
    with http.build_client() as client:
        for dept in depts:
            kept = fetch_stations(dept, client=client, root=root)
            files.append(root / "stations" / f"{dept}.jsonl")
            n_stations += len(kept)
            time.sleep(pause)
            for st in kept:
                path, n, status = fetch_chronique(st, client=client, root=root)
                files.append(path)
                n_chron += 1
                n_obs += n
                if status in ("downloaded", "incremental"):
                    time.sleep(pause)  # politesse : pause seulement sur les vrais GET

    manifest = write_manifest(
        SOURCE,
        root,
        source_url=_endpoint("stations"),
        srs="EPSG:4326",  # coords stations en WGS84 → reprojetées 2154 au staging
        source_version="Hub'eau niveaux_nappes (stations + chroniques)",
        files=files,
        extra={
            "departements": depts,
            "n_stations_gardees": n_stations,
            "n_chroniques": n_chron,
            "n_mesures": n_obs,
            "min_years": MIN_YEARS,
            "last_updated_ips": utc_now_iso(),
            "note_niveau": "niveau_nappe_eau = cote NGF (m) ; NGF haut = nappe haute = humide",
            "note_filtre": "chroniques ignore tout filtre géo → boucle par code_bss",
            "licence": LICENCE,
        },
    )
    log.info(
        "piezo.done",
        n_depts=len(depts),
        n_stations=n_stations,
        n_chroniques=n_chron,
        n_mesures=n_obs,
    )
    return RawDataset(SOURCE, root, files, manifest)


def main() -> None:
    fetch()


if __name__ == "__main__":
    main()
