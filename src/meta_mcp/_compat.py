"""Compatibilité entre les versions du SDK MCP.

Le SDK a renommé `FastMCP` (v1.x, module `mcp.server.fastmcp`) en `MCPServer`
(v2.x, module `mcp.server`). L'interface utilisée ici — décorateur `.tool()`,
`.list_tools()` et `.run()` — est identique dans les deux cas.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Le SDK v2.x est la cible de typage par défaut (décorateur `.tool()` typé).
    from mcp.server import MCPServer as MCPServer
else:
    try:  # SDK v2.x
        from mcp.server import MCPServer
    except ImportError:  # pragma: no cover - SDK v1.x
        from mcp.server.fastmcp import FastMCP as MCPServer

__all__ = ["MCPServer"]
