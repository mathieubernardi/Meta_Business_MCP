"""Outils d'analyse transverses (synthèses, classements)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .._compat import MCPServer
from ..client import MetaClient
from ..constants import (
    FB_PAGE_DAILY_METRICS,
    IG_ACCOUNT_DAILY_METRICS,
    IG_ACCOUNT_FIELDS,
    IG_MEDIA_FIELDS,
    IG_MEDIA_METRICS,
)
from .instagram import resolve_ig_user_id

JSONDict = dict[str, Any]


def _sum_series(values: list[JSONDict]) -> int:
    """Additionne les points d'une série quotidienne."""
    total = 0
    for point in values:
        value = point.get("value")
        if isinstance(value, (int, float)):
            total += int(value)
    return total


def register(mcp: MCPServer, client: MetaClient) -> None:
    """Enregistre les outils d'analyse sur le serveur MCP."""

    @mcp.tool()
    async def account_summary(page_id: str, days: int = 30) -> JSONDict:
        """Synthèse Facebook + Instagram sur N jours (portée, engagement, abonnés).

        Point d'entrée recommandé pour un bilan rapide.
        """
        until = date.today()
        since = until - timedelta(days=days)
        summary: JSONDict = {"period": {"since": since.isoformat(),
                                        "until": until.isoformat(), "days": days}}

        token = await client.page_token(page_id)
        facebook: JSONDict = {}
        for metric in FB_PAGE_DAILY_METRICS:
            try:
                data = await client.get(
                    f"{page_id}/insights",
                    {"metric": metric, "period": "day",
                     "since": since.isoformat(), "until": until.isoformat()},
                    token=token,
                )
            except Exception:  # noqa: BLE001 - métrique indisponible = ignorée
                continue
            for entry in data.get("data", []):
                facebook[entry["name"]] = _sum_series(entry.get("values", []))
        summary["facebook"] = facebook

        ig_id = await resolve_ig_user_id(client, page_id)
        if not ig_id:
            summary["instagram"] = {"error": "aucun compte Instagram Business lié"}
            return summary

        profile = await client.get(ig_id, {"fields": IG_ACCOUNT_FIELDS})
        instagram: JSONDict = {
            "username": profile.get("username"),
            "followers_count": profile.get("followers_count"),
            "media_count": profile.get("media_count"),
        }
        for metric in IG_ACCOUNT_DAILY_METRICS:
            params: JSONDict = {"metric": metric, "period": "day",
                                "since": since.isoformat(), "until": until.isoformat()}
            try:
                data = await client.get(f"{ig_id}/insights", params)
            except Exception:  # noqa: BLE001
                try:
                    data = await client.get(
                        f"{ig_id}/insights", {**params, "metric_type": "total_value"}
                    )
                except Exception:  # noqa: BLE001
                    continue
            for entry in data.get("data", []):
                instagram[entry["name"]] = _sum_series(entry.get("values", []))
        summary["instagram"] = instagram
        return summary

    @mcp.tool()
    async def top_ig_posts(
        ig_user_id: str, limit: int = 10, sort_by: str = "reach"
    ) -> JSONDict:
        """Classe les publications Instagram par performance.

        `sort_by` : reach, likes, comments, saved, shares ou total_interactions.
        """
        if sort_by not in IG_MEDIA_METRICS:
            return {"error": f"sort_by doit être parmi {list(IG_MEDIA_METRICS)}"}
        media = await client.paginate(
            f"{ig_user_id}/media", {"fields": IG_MEDIA_FIELDS, "limit": 50}
        )
        rows: list[JSONDict] = []
        for item in media[:50]:
            row: JSONDict = {
                "id": item.get("id"),
                "timestamp": item.get("timestamp"),
                "media_type": item.get("media_type"),
                "permalink": item.get("permalink"),
                "caption": (item.get("caption") or "")[:120],
            }
            try:
                insights = await client.get(
                    f"{item['id']}/insights", {"metric": ",".join(IG_MEDIA_METRICS)}
                )
                for entry in insights.get("data", []):
                    row[entry["name"]] = (entry.get("values") or [{}])[0].get("value")
            except Exception:  # noqa: BLE001 - média sans insights (trop ancien)
                pass
            rows.append(row)
        rows.sort(key=lambda r: r.get(sort_by) or 0, reverse=True)
        return {"sorted_by": sort_by, "count": len(rows), "top": rows[:limit]}

    @mcp.tool()
    async def follower_growth(page_id: str, days: int = 30) -> JSONDict:
        """Évolution quotidienne des abonnés de la Page Facebook."""
        until = date.today()
        since = until - timedelta(days=days)
        token = await client.page_token(page_id)
        data = await client.get(
            f"{page_id}/insights",
            {"metric": "page_daily_follows_unique", "period": "day",
             "since": since.isoformat(), "until": until.isoformat()},
            token=token,
        )
        points: list[JSONDict] = []
        for entry in data.get("data", []):
            for value in entry.get("values", []):
                points.append({"date": str(value.get("end_time", ""))[:10],
                               "new_followers": value.get("value")})
        return {"days": days, "total": _sum_series(
            [{"value": p["new_followers"]} for p in points]), "daily": points}
