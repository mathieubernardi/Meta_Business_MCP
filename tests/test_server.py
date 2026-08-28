"""Tests d'assemblage du serveur MCP."""

from __future__ import annotations

import pytest

from meta_mcp.config import Settings
from meta_mcp.server import build_server

EXPECTED_MINIMUM_TOOLS = 30


@pytest.fixture
def settings() -> Settings:
    return Settings(access_token="fake-token", enable_writes=False)


async def test_server_registers_tools(settings):
    mcp, client = build_server(settings)
    tools = await mcp.list_tools()
    assert len(tools) >= EXPECTED_MINIMUM_TOOLS
    await client.aclose()


async def test_core_tools_are_present(settings):
    mcp, client = build_server(settings)
    names = {tool.name for tool in await mcp.list_tools()}
    expected = {
        "health_check",
        "list_pages",
        "get_page_insights",
        "get_ig_account",
        "get_ig_media_insights",
        "account_summary",
        "top_ig_posts",
        "publish_page_post",
        "ig_publish_carousel",
        "publish_thread",
    }
    assert expected <= names
    await client.aclose()


async def test_every_tool_has_description(settings):
    mcp, client = build_server(settings)
    missing = [t.name for t in await mcp.list_tools() if not t.description]
    assert missing == []
    await client.aclose()
