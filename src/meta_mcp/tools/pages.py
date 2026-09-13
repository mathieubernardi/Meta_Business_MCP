"""Outils Facebook Pages (lecture)."""

from __future__ import annotations

from typing import Any

from .._compat import MCPServer
from ..client import MetaClient
from ..constants import FB_PAGE_DAILY_METRICS, FB_POST_METRICS, PAGE_FIELDS, POST_FIELDS

JSONDict = dict[str, Any]


def register(mcp: MCPServer, client: MetaClient) -> None:
    """Enregistre les outils Pages sur le serveur MCP."""

    @mcp.tool()
    async def list_pages() -> JSONDict:
        """Liste les Pages Facebook accessibles avec ce token (id, nom, abonnés)."""
        pages = await client.paginate("me/accounts", {"fields": PAGE_FIELDS})
        return {
            "count": len(pages),
            "pages": [
                {
                    "id": p.get("id"),
                    "name": p.get("name"),
                    "category": p.get("category"),
                    "followers_count": p.get("followers_count"),
                    "fan_count": p.get("fan_count"),
                    "link": p.get("link"),
                    "instagram_business_account": (
                        p.get("instagram_business_account", {}) or {}
                    ).get("id"),
                }
                for p in pages
            ],
        }

    @mcp.tool()
    async def get_page(page_id: str) -> JSONDict:
        """Détaille une Page Facebook (abonnés, description, compte Instagram lié)."""
        data = await client.get(page_id, {"fields": PAGE_FIELDS})
        data.pop("access_token", None)
        return data

    @mcp.tool()
    async def list_page_posts(page_id: str, limit: int = 25) -> JSONDict:
        """Liste les publications récentes d'une Page (message, date, lien, compteurs)."""
        token = await client.page_token(page_id)
        posts = await client.paginate(
            f"{page_id}/posts",
            {"fields": POST_FIELDS, "limit": min(limit, 100)},
            token=token,
            max_items=limit,
        )
        return {"count": len(posts), "posts": posts}

    @mcp.tool()
    async def get_post(post_id: str) -> JSONDict:
        """Récupère une publication Facebook précise."""
        return await client.get(post_id, {"fields": POST_FIELDS})

    @mcp.tool()
    async def list_post_comments(post_id: str, limit: int = 50) -> JSONDict:
        """Liste les commentaires d'une publication Facebook."""
        comments = await client.paginate(
            f"{post_id}/comments",
            {"fields": "id,from,message,created_time,like_count", "limit": min(limit, 100)},
            max_items=limit,
        )
        return {"count": len(comments), "comments": comments}

    @mcp.tool()
    async def get_page_insights(
        page_id: str,
        since: str,
        until: str,
        metrics: list[str] | None = None,
        period: str = "day",
    ) -> JSONDict:
        """Statistiques quotidiennes d'une Page (portée, engagement, abonnés).

        Dates au format AAAA-MM-JJ. Sans `metrics`, utilise le jeu par défaut.
        """
        token = await client.page_token(page_id)
        wanted = tuple(metrics) if metrics else FB_PAGE_DAILY_METRICS
        series: dict[str, list[JSONDict]] = {}
        errors: dict[str, str] = {}
        for metric in wanted:
            try:
                data = await client.get(
                    f"{page_id}/insights",
                    {"metric": metric, "period": period, "since": since, "until": until},
                    token=token,
                )
            except Exception as exc:  # noqa: BLE001 - on rapporte sans interrompre
                errors[metric] = str(exc)
                continue
            for entry in data.get("data", []):
                series[entry["name"]] = entry.get("values", [])
        return {"page_id": page_id, "since": since, "until": until,
                "metrics": series, "errors": errors}

    @mcp.tool()
    async def get_post_insights(post_id: str, metrics: list[str] | None = None) -> JSONDict:
        """Statistiques d'une publication Facebook (portée, clics, réactions)."""
        wanted = ",".join(metrics) if metrics else ",".join(FB_POST_METRICS)
        data = await client.get(f"{post_id}/insights", {"metric": wanted})
        return {
            entry["name"]: (entry.get("values") or [{}])[0].get("value")
            for entry in data.get("data", [])
        }
