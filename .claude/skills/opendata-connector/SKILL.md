---
name: opendata-connector
description: >
  Patron pour écrire un connecteur d'ingestion open data FR propre, idempotent et POLI (cache,
  backoff, pagination, bornage), avec écriture en zone brute + manifeste. Utilise systématiquement
  ce skill pour TOUT nouveau connecteur Solveille (Hub'eau, data.gouv, meteo.data.gouv, Géorisques,
  DVF…) ou pour corriger un connecteur instable, même si la tâche semble simple.
---

# Skill : connecteur open data FR (poli & idempotent)

Objectif : chaque source a un module `solveille/ingest/<source>.py` exposant une fonction `fetch()` qui télécharge le **brut**, écrit dans `data/raw/<source>/` et produit un **manifeste** `_meta.json`. Jamais de transformation lourde ici (ça vit dans `transform/`).

## Règles (le « pourquoi » compte)
1. **Idempotent** : relancer ne duplique pas ; nomme les fichiers de façon déterministe (date + version source) et écrase proprement le run du jour.
2. **Poli** : ces API publiques sont gratuites et partagées. Toujours : timeout, **retries avec backoff exponentiel**, `User-Agent` explicite, **cache** (réutilise si `ETag`/`Last-Modified` inchangé), et **bornage** des requêtes (bbox/`code_departement`/`size`) plutôt qu'un balayage massif. Respecte les quotas.
3. **Traçable** : écris `_meta.json` (source, url, date_fetch, version/horodatage source, nb_lignes, hash). Ces champs remontent jusqu'à l'UI (`last_updated_*`).
4. **Tolérant** : valide le **schéma** minimal attendu (champs/types) et échoue clairement si la structure a changé (plutôt que de produire des données fausses).
5. **SRS** : ne reprojette pas ici ; note le SRS d'origine dans le manifeste — la reprojection vers EPSG:2154 se fait en `transform/`.

## Étapes
1. Lis la fiche de la source dans `docs/data-sources.md` (et au besoin délègue au subagent `data-source-researcher`).
2. Implémente `fetch()` en t'appuyant sur l'utilitaire `scripts/fetch_paginated.py` (GET poli + pagination + cache). Pour Hub'eau, suis la pagination par `page`/`size` ; pour data.gouv, résous d'abord la dernière ressource.
3. Écris le brut + `_meta.json`. Logue (structuré) début/fin/volumétrie.
4. Ajoute un test : le parsing renvoie le schéma attendu sur un échantillon.

## Exemple d'interface
```python
# solveille/ingest/hubeau_piezo.py
from solveille.common.raw import write_raw, manifest

def fetch(departements: list[str]) -> None:
    for dep in departements:                      # bornage poli
        rows = get_paginated(
            "https://hubeau.eaufrance.fr/api/v1/niveaux_nappes/chroniques_tr",
            params={"code_departement": dep, "size": 5000},
            page_param="page", follow="next",      # voir scripts/fetch_paginated.py
        )
        write_raw("hubeau_piezo", f"{dep}.jsonl", rows)
    manifest("hubeau_piezo", source_url="hubeau…", srs="WGS84")
```

## Anti-patterns à éviter
- Boucler sans backoff / sans bornage sur une API publique. Coder une URL de ressource en dur quand un lien stable existe. Mélanger fetch et transformation. Avaler une erreur de schéma.

Voir `scripts/fetch_paginated.py` pour l'utilitaire réutilisable.
