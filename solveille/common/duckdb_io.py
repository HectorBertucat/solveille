"""Connexion DuckDB préconfigurée : extensions `spatial`+`httpfs`, bornes mémoire VM.

Tout le géo-traitement passe par ici pour garantir des réglages homogènes
(EPSG:2154 au moment des jointures, streaming sur Parquet, RAM bornée à ~6 Go).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import duckdb


def install_extensions(con: duckdb.DuckDBPyConnection) -> None:
    """Installe/charge `spatial` et `httpfs` (téléchargées au 1er appel, puis cachées)."""
    con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")


def connect(
    *,
    database: str = ":memory:",
    read_only: bool = False,
    memory_limit: str = "6GB",
    threads: int = 4,
) -> duckdb.DuckDBPyConnection:
    """Ouvre une connexion DuckDB avec extensions et garde-fous ressources VM."""
    con = duckdb.connect(database, read_only=read_only)
    con.execute(f"PRAGMA threads={threads};")
    con.execute(f"SET memory_limit='{memory_limit}';")
    install_extensions(con)
    return con


@contextmanager
def connection(
    *,
    database: str = ":memory:",
    read_only: bool = False,
    memory_limit: str = "6GB",
    threads: int = 4,
) -> Iterator[duckdb.DuckDBPyConnection]:
    """Connexion DuckDB en gestionnaire de contexte (fermeture garantie)."""
    con = connect(
        database=database, read_only=read_only, memory_limit=memory_limit, threads=threads
    )
    try:
        yield con
    finally:
        con.close()
