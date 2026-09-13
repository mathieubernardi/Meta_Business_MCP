"""Outils Instagram Business (lecture)."""

from __future__ import annotations

from typing import Any

from .._compat import MCPServer
from ..client import MetaClient
from ..constants import (
    IG_ACCOUNT_DAILY_METRICS,
    IG_ACCOUNT_FIELDS,
    IG_DEMOGRAPHIC_BREAKDOWNS,
    IG_MEDIA_FIELDS,
    IG_MEDIA_METRICS,
    IG_STORY_METRICS,
)
from ..errors import ToolInputError

JSONDict = dict[str, Any]


async def resolve_ig_user_id(client: MetaClient, page_id: str | None = None) -> str | None:
    """Trouve l'ID du compte Instagram Business rattaché à une Page."""
    if page_id:
        data = await client.get(page_id, {"fields": "instagram_business_account"})
        account = data.get("instagram_business_account") or {}
        return account.get("id")
    pages = await client.paginate(
        "me/accounts", {"fields": "id,instagram_business_account"}
    )
    for page in pages:
        account = page.get("instagram_business_account") or {}
        if account.get("id"):
            return str(account["id"])
    return None


def register(mcp: MCPServer, client: MetaClient) -> None:
    """Enregistre les outils Instagram sur le serveur MCP."""

    @mcp.tool()
    async def find_instagram_account(page_id: str | None = None) -> JSONDict:
        """Trouve l'ID du compte Instagram Business lié (auto-découverte)."""
        ig_id = await resolve_ig_user_id(client, page_id)
        return {"ig_user_id": ig_id, "found": ig_id is not None}

    @mcp.tool()
    async def get_ig_account(ig_user_id: str) -> JSONDict:
        """Profil Instagram : abonnés, comptes suivis, nombre de publications."""
        return await client.get(ig_user_id, {"fields": IG_ACCOUNT_FIELDS})

    @mcp.tool()
    async def list_ig_media(ig_user_id: str, limit: int = 25) -> JSONDict:
        """Liste les publications Instagram récentes (posts, reels, carrousels)."""
        media = await client.paginate(
            f"{ig_user_id}/media",
            {"fields": IG_MEDIA_FIELDS, "limit": min(limit, 100)},
            max_items=limit,
        )
        return {"count": len(media), "media": media}

    @mcp.tool()
    async def get_ig_media(media_id: str) -> JSONDict:
        """Détaille une publication Instagram."""
        return await client.get(media_id, {"fields": IG_MEDIA_FIELDS})

    @mcp.tool()
    async def get_ig_media_insights(
        media_id: str, metrics: list[str] | None = None
    ) -> JSONDict:
        """Statistiques d'une publication Instagram (portée, likes, saves, partages)."""
        wanted = ",".join(metrics) if metrics else ",".join(IG_MEDIA_METRICS)
        data = await client.get(f"{media_id}/insights", {"metric": wanted})
        return {
            entry["name"]: (entry.get("values") or [{}])[0].get("value")
            for entry in data.get("data", [])
        }

    @mcp.tool()
    async def get_ig_account_insights(
        ig_user_id: str,
        since: str,
        until: str,
        metrics: list[str] | None = None,
    ) -> JSONDict:
        """Statistiques quotidiennes du compte Instagram (portée, vues de profil).

        Dates au format AAAA-MM-JJ.
        """
        wanted = tuple(metrics) if metrics else IG_ACCOUNT_DAILY_METRICS
        series: dict[str, list[JSONDict]] = {}
        errors: dict[str, str] = {}
        for metric in wanted:
            params: JSONDict = {
                "metric": metric, "period": "day", "since": since, "until": until,
            }
            try:
                data = await client.get(f"{ig_user_id}/insights", params)
            except Exception:  # noqa: BLE001 - certaines versions exigent metric_type
                try:
                    data = await client.get(
                        f"{ig_user_id}/insights", {**params, "metric_type": "total_value"}
                    )
                except Exception as exc:  # noqa: BLE001
                    errors[metric] = str(exc)
                    continue
            for entry in data.get("data", []):
                series[entry["name"]] = entry.get("values", [])
        return {"ig_user_id": ig_user_id, "since": since, "until": until,
                "metrics": series, "errors": errors}

    @mcp.tool()
    async def get_ig_audience_demographics(
        ig_user_id: str, breakdown: str = "age,gender"
    ) -> JSONDict:
        """Démographie des abonnés Instagram (âge/genre, pays ou ville)."""
        if breakdown not in IG_DEMOGRAPHIC_BREAKDOWNS:
            raise ToolInputError(
                f"breakdown doit être parmi {list(IG_DEMOGRAPHIC_BREAKDOWNS)}"
            )
        data = await client.get(
            f"{ig_user_id}/insights",
            {
                "metric": "follower_demographics",
                "period": "lifetime",
                "metric_type": "total_value",
                "breakdown": breakdown,
            },
        )
        return data

    @mcp.tool()
    async def list_ig_stories(ig_user_id: str) -> JSONDict:
        """Liste les stories Instagram actives (24 h)."""
        stories = await client.paginate(
            f"{ig_user_id}/stories", {"fields": IG_MEDIA_FIELDS}
        )
        return {"count": len(stories), "stories": stories}

    @mcp.tool()
    async def get_ig_story_insights(story_id: str) -> JSONDict:
        """Statistiques d'une story Instagram (portée, réponses, navigation)."""
        data = await client.get(
            f"{story_id}/insights", {"metric": ",".join(IG_STORY_METRICS)}
        )
        return {
            entry["name"]: (entry.get("values") or [{}])[0].get("value")
            for entry in data.get("data", [])
        }

    @mcp.tool()
    async def list_ig_comments(media_id: str, limit: int = 50) -> JSONDict:
        """Liste les commentaires d'une publication Instagram."""
        comments = await client.paginate(
            f"{media_id}/comments",
            {"fields": "id,text,username,timestamp,like_count", "limit": min(limit, 100)},
            max_items=limit,
        )
        return {"count": len(comments), "comments": comments}
