"""Tests des outils de lecture : pagination bornée par `limit`, classement des publications."""

from __future__ import annotations

import re

import httpx
import pytest
import respx

from meta_mcp.config import Settings
from meta_mcp.constants import GRAPH_API_BASE
from meta_mcp.server import build_server


@pytest.fixture
async def server():
    mcp, client = build_server(Settings(access_token="fake-token"))
    yield mcp
    await client.aclose()


def _rows(prefix: str, count: int) -> list[dict[str, str]]:
    return [{"id": f"{prefix}{index}"} for index in range(count)]


def _insights_from_id(request: httpx.Request) -> httpx.Response:
    """Portée égale au numéro du média : `.../m42/insights` -> reach 42."""
    media_id = request.url.path.split("/")[-2]
    reach = int(media_id.removeprefix("m"))
    return httpx.Response(
        200, json={"data": [{"name": "reach", "values": [{"value": reach}]}]}
    )


@respx.mock
async def test_list_page_posts_stops_paginating_once_limit_is_reached(server):
    """Régression : `limit` n'arrêtait pas la pagination (jusqu'à 10 pages chargées)
    et `count` annonçait plus d'éléments que la liste renvoyée."""
    respx.get(f"{GRAPH_API_BASE}/123").mock(
        return_value=httpx.Response(200, json={"access_token": "page-token"})
    )
    respx.get(f"{GRAPH_API_BASE}/123/posts").mock(
        return_value=httpx.Response(
            200,
            json={"data": _rows("p", 3), "paging": {"next": f"{GRAPH_API_BASE}/123/posts_page2"}},
        )
    )
    page2 = respx.get(f"{GRAPH_API_BASE}/123/posts_page2").mock(
        return_value=httpx.Response(200, json={"data": _rows("q", 3)})
    )

    result = await server.call_tool("list_page_posts", {"page_id": "123", "limit": 2})

    payload = result.structured_content
    assert payload["count"] == 2
    assert [post["id"] for post in payload["posts"]] == ["p0", "p1"]
    assert not page2.called


@respx.mock
async def test_top_ig_posts_loads_a_single_sample_and_ranks_it(server):
    """Régression : jusqu'à 500 médias chargés pour n'en classer que 50."""
    respx.get(f"{GRAPH_API_BASE}/999/media").mock(
        return_value=httpx.Response(
            200,
            json={"data": _rows("m", 50), "paging": {"next": f"{GRAPH_API_BASE}/999/media_page2"}},
        )
    )
    page2 = respx.get(f"{GRAPH_API_BASE}/999/media_page2").mock(
        return_value=httpx.Response(200, json={"data": _rows("n", 50)})
    )
    respx.get(url__regex=rf"{re.escape(GRAPH_API_BASE)}/m\d+/insights").mock(
        side_effect=_insights_from_id
    )

    result = await server.call_tool("top_ig_posts", {"ig_user_id": "999", "limit": 3})

    payload = result.structured_content
    assert payload["count"] == 50
    assert [row["id"] for row in payload["top"]] == ["m49", "m48", "m47"]
    assert not page2.called


@respx.mock
async def test_top_ig_posts_flags_media_without_insights(server):
    """Un média sans statistiques reste classé, mais l'erreur est visible au lieu
    d'être avalée (sinon il passe pour un média à portée nulle)."""
    respx.get(f"{GRAPH_API_BASE}/999/media").mock(
        return_value=httpx.Response(200, json={"data": _rows("m", 2)})
    )
    respx.get(f"{GRAPH_API_BASE}/m0/insights").mock(
        return_value=httpx.Response(
            200, json={"data": [{"name": "reach", "values": [{"value": 5}]}]}
        )
    )
    respx.get(f"{GRAPH_API_BASE}/m1/insights").mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "média trop ancien", "type": "OAuthException"}}
        )
    )

    result = await server.call_tool("top_ig_posts", {"ig_user_id": "999"})

    rows = {row["id"]: row for row in result.structured_content["top"]}
    assert rows["m0"]["reach"] == 5
    assert "insights_error" not in rows["m0"]
    assert "média trop ancien" in rows["m1"]["insights_error"]
