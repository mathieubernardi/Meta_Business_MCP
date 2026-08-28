"""Outils Threads (lecture)."""

from __future__ import annotations

from typing import Any

from .._compat import MCPServer
from ..client import MetaClient
from ..constants import THREADS_MEDIA_FIELDS, THREADS_PROFILE_FIELDS

JSONDict = dict[str, Any]


def register(mcp: MCPServer, client: MetaClient) -> None:
    """Enregistre les outils Threads sur le serveur MCP."""

    @mcp.tool()
    async def get_threads_profile() -> JSONDict:
        """Profil Threads associé au token."""
        return await client.threads_get("me", {"fields": THREADS_PROFILE_FIELDS})

    @mcp.tool()
    async def list_threads_posts(limit: int = 25) -> JSONDict:
        """Liste les publications Threads récentes."""
        data = await client.threads_get(
            "me/threads", {"fields": THREADS_MEDIA_FIELDS, "limit": min(limit, 100)}
        )
        posts = data.get("data", [])
        return {"count": len(posts), "posts": posts}

    @mcp.tool()
    async def get_threads_insights(thread_id: str) -> JSONDict:
        """Statistiques d'une publication Threads (vues, likes, réponses, partages)."""
        data = await client.threads_get(
            f"{thread_id}/insights",
            {"metric": "views,likes,replies,reposts,quotes,shares"},
        )
        return {
            entry["name"]: entry.get("total_value", {}).get("value")
            or (entry.get("values") or [{}])[0].get("value")
            for entry in data.get("data", [])
        }
