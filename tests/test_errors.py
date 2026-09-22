"""Contrat d'erreur : un outil qui ne peut pas aboutir lève une exception.

Côté protocole, le client MCP reçoit alors `isError: true` au lieu d'un faux
succès contenant une clé `error`.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from meta_mcp.client import GraphAPIError, WritesDisabledError
from meta_mcp.config import Settings
from meta_mcp.constants import GRAPH_API_BASE
from meta_mcp.errors import ContainerNotReadyError, OperationError, ToolInputError
from meta_mcp.server import build_server


@pytest.fixture
async def server():
    mcp, client = build_server(Settings(access_token="fake-token", enable_writes=True))
    yield mcp
    await client.aclose()


@pytest.mark.parametrize(
    "error_class",
    [GraphAPIError, WritesDisabledError, ToolInputError, OperationError, ContainerNotReadyError],
)
def test_anticipated_errors_are_sdk_tool_errors(error_class):
    """Régression (CI avec mcp 2.2) : seul le message d'un `ToolError` parvient au
    modèle ; toute autre exception est masquée derrière « Error executing tool <nom> »."""
    assert issubclass(error_class, ToolError)


INVALID_CALLS = [
    ("top_ig_posts", {"ig_user_id": "1", "sort_by": "popularite"}, "sort_by"),
    ("get_ig_audience_demographics", {"ig_user_id": "1", "breakdown": "age"}, "breakdown"),
    ("get_page_audience", {"page_id": "1", "breakdown": "age"}, "breakdown"),
    ("add_users_to_audience", {"audience_id": "1", "schema": "NOM", "values": ["a"]}, "schema"),
    ("add_users_to_audience", {"audience_id": "1", "schema": "EMAIL", "values": []}, "aucune"),
    ("remove_users_from_audience", {"audience_id": "1", "schema": "X", "values": ["a"]}, "schema"),
    ("remove_users_from_audience", {"audience_id": "1", "schema": "EMAIL", "values": []}, "aucune"),
    ("ig_publish_carousel", {"ig_user_id": "1", "image_urls": ["https://x/a"]}, "entre 2 et 10"),
    (
        "ig_publish_carousel_from_files",
        {"ig_user_id": "1", "page_id": "1", "file_paths": ["a.jpg"]},
        "entre 2 et 10",
    ),
    (
        "publish_page_carousel",
        {"page_id": "1", "image_urls": [], "message": "x"},
        "au moins une image",
    ),
    (
        "publish_page_carousel_from_files",
        {"page_id": "1", "file_paths": [], "message": "x"},
        "au moins une image",
    ),
]


@pytest.mark.parametrize(
    ("tool", "arguments", "message"),
    INVALID_CALLS,
    ids=[
        "sort_by",
        "ig_breakdown",
        "fb_breakdown",
        "add_schema",
        "add_empty",
        "remove_schema",
        "remove_empty",
        "carousel_count",
        "carousel_files_count",
        "fb_carousel_empty",
        "fb_carousel_files_empty",
    ],
)
@respx.mock
async def test_invalid_input_raises_before_any_request(server, tool, arguments, message):
    """Aucune requête n'est émise : respx lèverait sur tout appel non simulé."""
    with pytest.raises(ToolError, match=message) as exc_info:
        await server.call_tool(tool, arguments)
    assert isinstance(exc_info.value.__cause__, ToolInputError)


async def test_failure_reaches_mcp_client_as_is_error(server):
    """Régression : l'échec arrivait au client comme un succès (`isError` absent)."""
    async with Client(server) as mcp_client:
        result = await mcp_client.call_tool(
            "top_ig_posts", {"ig_user_id": "1", "sort_by": "popularite"}
        )
    assert result.is_error is True
    assert "sort_by" in result.content[0].text


@respx.mock
async def test_partial_failures_stay_in_the_result(server):
    """Une métrique indisponible n'est pas un échec de l'outil : elle reste dans la réponse."""
    respx.get(f"{GRAPH_API_BASE}/1").mock(
        return_value=httpx.Response(200, json={"access_token": "page-token"})
    )
    respx.get(f"{GRAPH_API_BASE}/1/insights").mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "métrique indisponible", "type": "OAuthException"}}
        )
    )
    result = await server.call_tool("audience_overview", {"page_id": "1"})
    overview = result.structured_content
    for label in ("country", "city", "age_gender", "locale"):
        assert "métrique indisponible" in overview[label]["error"]
