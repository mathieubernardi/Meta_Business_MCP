"""Serveur MCP Meta : assemble le client et les modules d'outils."""

from __future__ import annotations

import logging

from ._compat import MCPServer
from .client import MetaClient
from .config import Settings
from .tools import (
    analytics,
    audiences,
    instagram,
    pages,
    publishing,
    threads,
    utility,
)

LOG = logging.getLogger("meta_mcp")

INSTRUCTIONS = """Serveur MCP pour Meta Business (Facebook Pages, Instagram, Threads).

Démarrage conseillé : appelle `health_check` pour vérifier la configuration, puis
`list_pages` pour récupérer l'ID de ta Page, puis `account_summary` pour un bilan.

Sécurité : les outils de publication et de suppression sont bloqués tant que
META_ENABLE_WRITES=true n'est pas défini."""


def build_server(settings: Settings | None = None) -> tuple[MCPServer, MetaClient]:
    """Construit le serveur et enregistre tous les outils."""
    config = settings or Settings.from_env()
    client = MetaClient(
        config.access_token,
        config.threads_token,
        enable_writes=config.enable_writes,
    )
    mcp = MCPServer("meta-mcp-py", instructions=INSTRUCTIONS)

    for module in (utility, pages, instagram, threads, analytics, audiences, publishing):
        module.register(mcp, client)

    LOG.info(
        "Serveur prêt (écritures %s)",
        "activées" if config.enable_writes else "désactivées",
    )
    return mcp, client


def main() -> None:
    """Point d'entrée : lance le serveur sur stdio."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    mcp, _client = build_server()
    mcp.run()


if __name__ == "__main__":
    main()
