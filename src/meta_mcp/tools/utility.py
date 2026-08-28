"""Outils utilitaires : diagnostic, permissions, accès brut à l'API."""

from __future__ import annotations

from typing import Any

from .._compat import MCPServer
from ..client import MetaClient
from ..constants import GRAPH_API_VERSION

JSONDict = dict[str, Any]


def register(mcp: MCPServer, client: MetaClient) -> None:
    """Enregistre les outils utilitaires sur le serveur MCP."""

    @mcp.tool()
    async def health_check() -> JSONDict:
        """Diagnostic de configuration : token présent, écritures activées, identité."""
        checks: JSONDict = {
            "graph_api_version": GRAPH_API_VERSION,
            "META_ACCESS_TOKEN": "configuré" if client.has_token else "MANQUANT",
            "mode_ecriture": "ACTIVÉ" if client.writes_enabled else "désactivé (lecture seule)",
        }
        if not client.has_token:
            checks["hint"] = "Ajoute META_ACCESS_TOKEN dans la config MCP ou un .env"
            return checks
        try:
            me = await client.get("me", {"fields": "id,name"})
            checks["identite"] = me
        except Exception as exc:  # noqa: BLE001 - diagnostic : on rapporte l'erreur
            checks["identite"] = f"erreur : {exc}"
        return checks

    @mcp.tool()
    async def token_permissions() -> JSONDict:
        """Liste les permissions accordées au token et leur statut."""
        data = await client.get("me/permissions")
        granted = [p["permission"] for p in data.get("data", [])
                   if p.get("status") == "granted"]
        declined = [p["permission"] for p in data.get("data", [])
                    if p.get("status") != "granted"]
        return {"granted": granted, "declined": declined, "count": len(granted)}

    @mcp.tool()
    async def token_info() -> JSONDict:
        """Informations sur le token : type, expiration, application, portées."""
        data = await client.get("debug_token", {"input_token": "SELF"})
        info = data.get("data", {})
        return {
            "type": info.get("type"),
            "app_id": info.get("app_id"),
            "expires_at": info.get("expires_at"),
            "is_valid": info.get("is_valid"),
            "scopes": info.get("scopes"),
        }

    @mcp.tool()
    async def graph_get(path: str, fields: str | None = None,
                        params: dict[str, str] | None = None) -> JSONDict:
        """Appel GET brut sur la Graph API (échappatoire pour un endpoint non couvert).

        Exemple : path="me/accounts", fields="id,name".
        """
        query: JSONDict = dict(params or {})
        if fields:
            query["fields"] = fields
        return await client.get(path, query)
