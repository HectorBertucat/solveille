"""Tests du staging SWI : grille, série maille, climatologie, anomalie standardisée.

Données synthétiques (vérifiables à la main) écrites en tmp_path : on contrôle que `z_SWI`
est bien une anomalie standardisée vs la climatologie du **même mois calendaire**, que
`std=0` donne `z` NULL, et que la grille est lue malgré ses lignes de commentaire `#`.
"""

from __future__ import annotations

import gzip
from pathlib import Path
from typing import Any

import pytest

from solveille.common import duckdb_io
from solveille.transform import staging

# Grille : 5 lignes de commentaire '#' (dont l'en-tête), puis
# num_maille;lambx;lamby;lambx93;lamby93;lat;lon
_GRILLE = """\
#num_maille : numéro de la maille
#lambx,lamby : Lambert 2 étendu (hectomètres)
#lambx93, lamby93 : Lambert 93
#lat_dg, lon_dg : degrés
#num_maille;lambx;lamby;lambx93;lamby93;lat_dg;lon_dg
1;5880;26730;641374;7106309;51.05;2.17
2;5960;26730;649370;7106242;51.05;2.28
"""

# SWI : en-tête entre guillemets ; DATE = AAAAMM. Climato sur 3 ans (2000-2002).
#  maille 1, janvier : 0.2/0.4/0.6 -> mean 0.4, std_samp 0.2 -> z(0.2)=-1, z(0.6)=+1
#  maille 1, juillet : 0.5/0.5/0.5 -> std 0 -> z NULL
#  maille 2, janvier : 0.1/0.1/0.4
_SWI_ROWS = [
    (1, 200001, 0.2),
    (1, 200101, 0.4),
    (1, 200201, 0.6),
    (1, 200007, 0.5),
    (1, 200107, 0.5),
    (1, 200207, 0.5),
    (2, 200001, 0.1),
    (2, 200101, 0.1),
    (2, 200201, 0.4),
]


@pytest.fixture
def swi_raw(tmp_path: Path) -> Path:
    (tmp_path / "grille_mailles.csv").write_text(_GRILLE, encoding="utf-8")
    body = '"NUMERO";"LAMBX";"LAMBY";"DATE";"SWI_UNIF_MENS"\n' + "".join(
        f"{m};641374;7106309;{d};{v}\n" for m, d, v in _SWI_ROWS
    )
    with gzip.open(tmp_path / "swi.200001-200212.csv.gz", "wt", encoding="utf-8") as fh:
        fh.write(body)
    return tmp_path


def _one(out: Path, where: str, cols: str = "z_swi") -> Any:
    con = duckdb_io.connect()
    try:
        return con.execute(f"SELECT {cols} FROM read_parquet('{out}') WHERE {where}").fetchone()
    finally:
        con.close()


def test_swi_grille_parses_comments_and_l93(swi_raw: Path, tmp_path: Path) -> None:
    con = duckdb_io.connect()
    try:
        out = staging.build_swi_grille(
            con, grille_csv=swi_raw / "grille_mailles.csv", out=tmp_path / "g.parquet"
        )
        rows = con.execute(
            f"SELECT num_maille, x93, y93 FROM read_parquet('{out}') ORDER BY num_maille"
        ).fetchall()
    finally:
        con.close()
    assert rows == [(1, 641374.0, 7106309.0), (2, 649370.0, 7106242.0)]  # lambx93/lamby93 (m)


def test_swi_maille_parses_date_aaaamm(swi_raw: Path, tmp_path: Path) -> None:
    con = duckdb_io.connect()
    try:
        out = staging.build_swi_maille(con, raw_dir=swi_raw, out=tmp_path / "m.parquet")
        n, dmin, dmax = con.execute(
            f"SELECT count(*), min(date_mois), max(date_mois) FROM read_parquet('{out}')"
        ).fetchone()
    finally:
        con.close()
    assert n == len(_SWI_ROWS)
    assert str(dmin) == "2000-01-01" and str(dmax) == "2002-07-01"


def test_swi_clim_and_anomalie(swi_raw: Path, tmp_path: Path) -> None:
    con = duckdb_io.connect()
    try:
        m = staging.build_swi_maille(con, raw_dir=swi_raw, out=tmp_path / "m.parquet")
        c = staging.build_swi_clim(con, maille_parquet=m, out=tmp_path / "c.parquet")
        a = staging.build_swi_anomalie(
            con,
            maille_parquet=m,
            clim_parquet=c,
            out=tmp_path / "a.parquet",
            served_from="2000-01-01",
        )
        clim_jan = con.execute(
            f"SELECT round(swi_mean,4), round(swi_std,4), n "
            f"FROM read_parquet('{c}') WHERE num_maille=1 AND mois_cal=1"
        ).fetchone()
        # même (maille, mois) : moyenne ~0, var ~1 sur la climatologie elle-même
        agg = con.execute(
            f"SELECT round(avg(z_swi),6), round(stddev_samp(z_swi),4) "
            f"FROM read_parquet('{a}') WHERE num_maille=2 AND month(date_mois)=1"
        ).fetchone()
    finally:
        con.close()

    assert clim_jan == (0.4, 0.2, 3)  # mean 0.4, std_samp 0.2, 3 ans
    # z standardisé vs le MÊME mois : z(maille1, jan 2000, swi 0.2) = (0.2-0.4)/0.2 = -1.0
    assert _one(a, "num_maille=1 AND date_mois=DATE '2000-01-01'", "round(z_swi,4)")[0] == -1.0
    assert _one(a, "num_maille=1 AND date_mois=DATE '2002-01-01'", "round(z_swi,4)")[0] == 1.0
    # std=0 (maille 1, juillet) -> z NULL (pas de division par ~0)
    assert _one(a, "num_maille=1 AND date_mois=DATE '2000-07-01'")[0] is None
    assert abs(agg[0]) < 1e-9 and agg[1] == 1.0
