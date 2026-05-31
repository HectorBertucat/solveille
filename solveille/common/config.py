"""Configuration Solveille, chargée depuis l'environnement et `.env`.

Les noms de variables suivent `.env.example` (pas de préfixe uniforme), d'où les
`validation_alias` explicites. Le bornage par départements est exposé via la
propriété `departements` (parsing robuste d'une chaîne `31,09,...`, commentaire
inline toléré).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

#: User-Agent explicite et identifiable pour toutes les requêtes sortantes.
USER_AGENT = "solveille/0.1 (+https://github.com/hectorbertucat/solveille)"

#: Début de la fenêtre temporelle SERVIE (curseur de carte, mart mensuel, tuiles). La
#: **climatologie** SWI utilise tout l'historique disponible — c'est seulement la plage
#: navigée/affichée. Voir ADR-016. Le dernier mois est dynamique (max des données).
SWI_SERVED_FROM = "2017-01-01"

#: Début de la fenêtre de **calibration `H`** (v2) : on calcule les `z_SWI` communaux passés
#: jusqu'ici pour caractériser la sévérité des évènements Cat-Nat sécheresse reconnus (les
#: premières reconnaissances datent de 1990). Distinct de la fenêtre SERVIE (2017→) et de la
#: climatologie (tout l'historique). Voir `docs/metric.md §H`, ADR-019.
SWI_CALIB_FROM = "1990-01-01"


class Settings(BaseSettings):
    """Réglages d'ingestion et de calcul (immuables après chargement)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    data_dir: Path = Field(default=Path("./data"), validation_alias="SOLVEILLE_DATA_DIR")
    # Liste brute de départements de bornage (vide = national) ; voir `.departements`.
    departements_raw: str = Field(default="", validation_alias="SOLVEILLE_DEPARTEMENTS")

    http_timeout: float = Field(default=30.0, validation_alias="HTTP_TIMEOUT")
    http_max_retries: int = Field(default=5, validation_alias="HTTP_MAX_RETRIES")
    http_pause_s: float = Field(default=0.2, validation_alias="HTTP_PAUSE_S")

    hubeau_base: str = Field(
        default="https://hubeau.eaufrance.fr/api/v1", validation_alias="HUBEAU_BASE"
    )

    @property
    def departements(self) -> list[str]:
        """Codes département bornant les ingestions (ordre préservé, sans doublon)."""
        raw = self.departements_raw.split("#", 1)[0]  # tolère un commentaire inline
        out: list[str] = []
        for tok in raw.split(","):
            code = tok.strip().upper()
            if code and code not in out:
                out.append(code)
        return out

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def staging_dir(self) -> Path:
        return self.data_dir / "staging"

    @property
    def marts_dir(self) -> Path:
        return self.data_dir / "marts"

    def source_raw_dir(self, source: str) -> Path:
        """Répertoire brut horodaté d'une source : `data/raw/<source>/`."""
        return self.raw_dir / source


@lru_cache
def get_settings() -> Settings:
    """Singleton de configuration (mis en cache après le premier appel)."""
    return Settings()
