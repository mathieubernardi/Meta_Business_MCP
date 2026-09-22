"""Tests du client Graph API et du garde-fou d'écriture."""

from __future__ import annotations

import logging

import httpx
import pytest
import respx

from meta_mcp.client import GraphAPIError, MetaClient, WritesDisabledError
from meta_mcp.config import Settings
from meta_mcp.constants import GRAPH_API_BASE


@pytest.fixture
async def client():
    instance = MetaClient("fake-token")
    yield instance
    await instance.aclose()


async def test_missing_token_raises():
    empty = MetaClient("")
    with pytest.raises(GraphAPIError, match="META_ACCESS_TOKEN"):
        await empty.get("me")
    await empty.aclose()


@respx.mock
async def test_get_sends_token_in_authorization_header(client):
    route = respx.get(f"{GRAPH_API_BASE}/me").mock(
        return_value=httpx.Response(200, json={"id": "42", "name": "LawMaster"})
    )
    data = await client.get("me", {"fields": "id,name"})
    assert data["name"] == "LawMaster"
    assert route.calls[0].request.headers["Authorization"] == "Bearer fake-token"


@respx.mock
async def test_token_never_appears_in_urls_or_logs(client, caplog):
    """Régression : le token passé dans l'URL finissait dans les logs INFO de httpx."""
    caplog.set_level(logging.INFO, logger="httpx")
    # Meta renvoie des URLs `paging.next` qui embarquent le token.
    next_url = f"{GRAPH_API_BASE}/me/posts_page2?access_token=fake-token&after=abc"
    respx.get(f"{GRAPH_API_BASE}/me/posts").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": "1"}], "paging": {"next": next_url}}
        )
    )
    respx.get(f"{GRAPH_API_BASE}/me/posts_page2").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "2"}]})
    )
    respx.post(f"{GRAPH_API_BASE}/me/feed").mock(
        return_value=httpx.Response(200, json={"id": "1"})
    )
    respx.delete(f"{GRAPH_API_BASE}/1").mock(
        return_value=httpx.Response(200, json={"success": True})
    )

    rows = await client.paginate("me/posts")
    await client.post("me/feed", {"message": "hello"})
    await client.delete("1")

    assert [r["id"] for r in rows] == ["1", "2"]
    assert "HTTP Request" in caplog.text  # les logs httpx sont bien capturés
    assert "fake-token" not in caplog.text
    for call in respx.calls:
        assert "fake-token" not in str(call.request.url)
        assert call.request.headers["Authorization"] == "Bearer fake-token"


@respx.mock
async def test_graph_error_is_wrapped(client):
    respx.get(f"{GRAPH_API_BASE}/me").mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "Invalid token", "type": "OAuthException"}}
        )
    )
    with pytest.raises(GraphAPIError, match="Invalid token"):
        await client.get("me")


@respx.mock
async def test_paginate_follows_next(client):
    # Page 2 sur un chemin distinct : respx ne différencie pas la query string seule.
    page2 = f"{GRAPH_API_BASE}/me/posts_page2"
    respx.get(f"{GRAPH_API_BASE}/me/posts").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": "1"}], "paging": {"next": page2}}
        )
    )
    respx.get(page2).mock(return_value=httpx.Response(200, json={"data": [{"id": "2"}]}))
    rows = await client.paginate("me/posts")
    assert [r["id"] for r in rows] == ["1", "2"]


@respx.mock
async def test_paginate_respects_max_pages(client):
    """Une pagination qui boucle sur elle-même s'arrête à max_pages."""
    respx.get(f"{GRAPH_API_BASE}/me/posts").mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"id": "1"}], "paging": {"next": f"{GRAPH_API_BASE}/me/posts"}},
        )
    )
    rows = await client.paginate("me/posts", max_pages=3)
    assert len(rows) == 3


@respx.mock
async def test_paginate_raises_when_a_following_page_fails(client):
    """Régression : une page suivante en erreur tronquait le résultat sans le signaler."""
    page2 = f"{GRAPH_API_BASE}/me/posts_page2"
    respx.get(f"{GRAPH_API_BASE}/me/posts").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": "1"}], "paging": {"next": page2}}
        )
    )
    respx.get(page2).mock(
        return_value=httpx.Response(
            429, json={"error": {"message": "rate limit", "type": "OAuthException"}}
        )
    )
    with pytest.raises(GraphAPIError, match="rate limit"):
        await client.paginate("me/posts")


@respx.mock
async def test_paginate_stops_once_max_items_are_collected(client):
    """Régression : la pagination chargeait jusqu'à 10 pages, quel que soit le besoin."""
    respx.get(f"{GRAPH_API_BASE}/me/posts").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [{"id": "1"}, {"id": "2"}, {"id": "3"}],
                "paging": {"next": f"{GRAPH_API_BASE}/me/posts_page2"},
            },
        )
    )
    page2 = respx.get(f"{GRAPH_API_BASE}/me/posts_page2").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "4"}]})
    )
    rows = await client.paginate("me/posts", max_items=2)
    assert [r["id"] for r in rows] == ["1", "2"]
    assert not page2.called


async def test_writes_blocked_by_default(client):
    assert client.writes_enabled is False
    with pytest.raises(WritesDisabledError, match="META_ENABLE_WRITES"):
        client.require_writes("publish_page_post")


async def test_writes_allowed_when_enabled():
    writable = MetaClient("fake-token", enable_writes=True)
    assert writable.writes_enabled is True
    writable.require_writes("publish_page_post")  # ne lève pas
    await writable.aclose()


@respx.mock
async def test_post_multipart_uploads_file(client):
    route = respx.post(f"{GRAPH_API_BASE}/123/photos").mock(
        return_value=httpx.Response(200, json={"id": "999"})
    )
    result = await client.post_multipart(
        "123/photos",
        {"published": "false"},
        files={"source": ("photo.jpg", b"fake-bytes", "image/jpeg")},
    )
    assert result == {"id": "999"}
    request = route.calls[0].request
    assert b"fake-bytes" in request.content
    assert b'name="source"' in request.content
    assert b'filename="photo.jpg"' in request.content


@respx.mock
async def test_page_token_is_cached(client):
    route = respx.get(f"{GRAPH_API_BASE}/123").mock(
        return_value=httpx.Response(200, json={"access_token": "page-token"})
    )
    assert await client.page_token("123") == "page-token"
    assert await client.page_token("123") == "page-token"
    assert route.call_count == 1


def test_settings_parse_write_flag(monkeypatch):
    monkeypatch.setenv("META_ACCESS_TOKEN", "abc")
    monkeypatch.setenv("META_ENABLE_WRITES", "true")
    assert Settings.from_env().enable_writes is True
    monkeypatch.setenv("META_ENABLE_WRITES", "false")
    assert Settings.from_env().enable_writes is False
