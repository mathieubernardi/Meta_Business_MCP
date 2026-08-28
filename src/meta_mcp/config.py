"""Configuration du serveur, lue depuis l'environnement."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path = Path(".env")) -> None:
    """Charge un fichier .env minimal (KEY=VALUE), sans dépendance externe."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True, slots=True)
class Settings:
    """Paramètres du serveur MCP.

    `enable_writes` est un garde-fou : tant qu'il est faux, aucun outil de
    publication ou de suppression ne peut s'exécuter.
    """

    access_token: str
    threads_token: str | None = None
    enable_writes: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        flag = os.environ.get("META_ENABLE_WRITES", "").strip().lower()
        return cls(
            access_token=os.environ.get("META_ACCESS_TOKEN", ""),
            threads_token=os.environ.get("THREADS_ACCESS_TOKEN") or None,
            enable_writes=flag in {"1", "true", "yes", "oui"},
        )
