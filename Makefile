# Makefile — interface de commandes Solveille.
# Les cibles `fetch-*`, `build`, `tiles`, `api` appellent le package Python à créer (voir docs/architecture.md).
# Ce fichier est un CONTRAT d'interface : Claude Code implémente les modules derrière.

.PHONY: setup lint test fetch-all fetch-v0 build build-swi build-piezo build-gaspar tiles search glyphs basemap api clean \
        fetch-communes fetch-rga fetch-bascule fetch-insee \
        fetch-swi fetch-piezo fetch-gaspar fetch-dvf fetch-fideli fetch-cp

setup:        ## venv + deps + extensions DuckDB
	uv sync --extra dev
	uv run python -c "import duckdb; con=duckdb.connect(); con.execute('INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;'); print('DuckDB spatial OK')"

lint:         ## ruff + mypy
	uv run ruff check . && uv run ruff format --check . && uv run mypy solveille

test:         ## pytest
	uv run pytest

# --- Ingestion (1 cible par source) ---
fetch-communes: ; uv run python -m solveille.ingest.admin_express
fetch-rga:      ; uv run python -m solveille.ingest.rga_2026
fetch-bascule:  ; uv run python -m solveille.ingest.communes_bascule
fetch-insee:    ; uv run python -m solveille.ingest.insee_logement
fetch-fideli:   ; uv run python -m solveille.ingest.fideli_epci
fetch-dvf:      ; uv run python -m solveille.ingest.dvf
fetch-swi:      ; uv run python -m solveille.ingest.swi_catnat
fetch-piezo:    ; uv run python -m solveille.ingest.hubeau_piezo
fetch-gaspar:   ; uv run python -m solveille.ingest.gaspar
fetch-cp:       ; uv run python -m solveille.ingest.codes_postaux
# v0 « carte de l'enjeu » (statique, sans la dynamique météo/nappes)
fetch-v0: fetch-communes fetch-rga fetch-bascule fetch-insee fetch-fideli fetch-dvf
fetch-all: fetch-communes fetch-rga fetch-fideli fetch-dvf fetch-swi fetch-piezo fetch-gaspar fetch-cp

build:        ## transformations DuckDB complètes + mart commune_pression
	uv run python -m solveille.transform.build

build-swi:    ## refresh SWI léger (mensuel) : dynamique uniquement (réutilise staging v0)
	uv run python -m solveille.transform.build swi

build-piezo:  ## refresh IPS léger (quotidien) : piézo + mart (réutilise staging v0/SWI)
	uv run python -m solveille.transform.build piezo

build-gaspar: ## refresh GASPAR léger (hebdo) : catnat + H + mart (réutilise commune_swi_hist)
	uv run python -m solveille.transform.build gaspar

tiles: glyphs ## génère les PMTiles communes (tippecanoe) + l'index de recherche (glyphs en prérequis)
	uv run python -m solveille.transform.tiles
	uv run python -m solveille.transform.build_search

search:       ## (re)génère uniquement l'index de recherche communal (front/communes-index.json)
	uv run python -m solveille.transform.build_search

glyphs:       ## télécharge les glyphs PBF (Noto Sans) du fond vectoriel → front/glyphs/ (idempotent)
	uv run python -m solveille.transform.build_glyphs

basemap:      ## construit le fond vectoriel France (Protomaps) → tiles/out/france.pmtiles (go-pmtiles)
	deploy/build-basemap.sh

api:          ## lance FastAPI en local
	uv run uvicorn solveille.api.main:app --reload

clean:        ## nettoie les artefacts (PAS data/raw)
	rm -rf data/staging data/marts tiles/out
