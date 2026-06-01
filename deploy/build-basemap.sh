#!/usr/bin/env bash
# Construit le FOND DE CARTE vectoriel France (B-vec) : extrait un sous-ensemble France
# métropolitaine + Corse du planet Protomaps (build.protomaps.com) vers tiles/out/france.pmtiles,
# servi ensuite par l'API (StaticFiles /tiles, requêtes Range) comme communes.pmtiles.
#
# Étape PONCTUELLE (le fond OSM bouge lentement) — à relancer manuellement ou via un timer mensuel.
# Idempotent et atomique (extraction dans un fichier temporaire puis `mv`). N'écrase pas l'existant
# si l'extraction échoue. AUCUNE compression du .pmtiles (casserait les requêtes Range).
#
# Réglages (env) :
#   BBOX     bbox lon/lat   (défaut : France métro + Corse)
#   MAXZOOM  zoom max       (défaut : 12 = zoom max interactif du front ; au-delà = tuiles jamais
#                            demandées). 13 pour une marge ; 11 pour un fichier plus léger.
#   PMTILES  binaire go-pmtiles (défaut : auto-détection PATH, sinon téléchargé sous .tools/)
set -euo pipefail

cd "${SOLVEILLE_REPO:-/opt/solveille}"

BBOX="${BBOX:--5.5,41.0,9.8,51.5}"
MAXZOOM="${MAXZOOM:-12}"
OUT="tiles/out/france.pmtiles"
GP_VERSION="1.30.3"

mkdir -p tiles/out .tools

# --- 1. Binaire go-pmtiles (utilise celui du PATH, sinon télécharge la release officielle) ---
PMTILES="${PMTILES:-$(command -v pmtiles || true)}"
if [ -z "${PMTILES}" ]; then
  PMTILES=".tools/pmtiles"
  if [ ! -x "${PMTILES}" ]; then
    os="$(uname -s)"; arch="$(uname -m)"
    case "${arch}" in x86_64|amd64) arch="x86_64";; arm64|aarch64) arch="arm64";; esac
    # Naming des assets go-pmtiles INCOHÉRENT selon l'OS (vérifié sur la release) : Darwin =
    # `go-pmtiles-<v>_Darwin_<arch>.zip` (tiret + zip) ; Linux/Windows = `go-pmtiles_<v>_<os>_<arch>.tar.gz`
    # (underscore + tar.gz). On gère les deux, sinon 404 sur la VM Linux.
    if [ "${os}" = "Darwin" ]; then
      url="https://github.com/protomaps/go-pmtiles/releases/download/v${GP_VERSION}/go-pmtiles-${GP_VERSION}_Darwin_${arch}.zip"
      echo "Téléchargement go-pmtiles : ${url}"
      curl -fsSL --max-time 180 -o .tools/gp.zip "${url}"
      unzip -o .tools/gp.zip pmtiles -d .tools >/dev/null
      rm -f .tools/gp.zip
    else
      url="https://github.com/protomaps/go-pmtiles/releases/download/v${GP_VERSION}/go-pmtiles_${GP_VERSION}_${os}_${arch}.tar.gz"
      echo "Téléchargement go-pmtiles : ${url}"
      curl -fsSL --max-time 180 -o .tools/gp.tgz "${url}"
      tar -xzf .tools/gp.tgz -C .tools pmtiles
      rm -f .tools/gp.tgz
    fi
    chmod +x "${PMTILES}"
  fi
fi
echo "go-pmtiles : $("${PMTILES}" version)"

# --- 2. Dernier build planet disponible (build.protomaps.com garde ~6 jours ; on sonde en arrière) ---
date_bin="date"
build_date=""
for i in $(seq 0 12); do
  d="$(${date_bin} -u -d "-${i} day" +%Y%m%d 2>/dev/null || ${date_bin} -u -v-"${i}"d +%Y%m%d)"
  if curl -fsI --max-time 15 "https://build.protomaps.com/${d}.pmtiles" >/dev/null 2>&1; then
    build_date="${d}"; break
  fi
done
[ -n "${build_date}" ] || { echo "ERREUR : aucun build Protomaps récent trouvé" >&2; exit 1; }
src="https://build.protomaps.com/${build_date}.pmtiles"
echo "Source planet : ${src}  | bbox=${BBOX} maxzoom=${MAXZOOM}"

# --- 3. Extraction atomique ---
tmp="${OUT}.tmp.$$"
trap 'rm -f "${tmp}"' EXIT
"${PMTILES}" extract "${src}" "${tmp}" --bbox="${BBOX}" --maxzoom="${MAXZOOM}"
mv -f "${tmp}" "${OUT}"
trap - EXIT

echo "OK → ${OUT} ($(du -h "${OUT}" | cut -f1), planet ${build_date}, z0-${MAXZOOM})"
