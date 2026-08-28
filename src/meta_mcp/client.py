"""Client asynchrone et typé pour la Graph API de Meta."""

from __future__ import annotations

from types import TracebackType
from typing import Any, Final

import httpx

from .constants import GRAPH_API_BASE, THREADS_API_BASE, TIMEOUT_SECONDS

JSONDict = dict[str, Any]

_TOKEN_ERROR: Final[str] = (
    "META_ACCESS_TOKEN n'est pas configuré. Ajoute-le dans la config MCP "
    "(bloc env) ou dans un fichier .env. Génère un token sur "
    "https://developers.facebook.com/tools/explorer/"
)


class GraphAPIError(RuntimeError):
    """Erreur renvoyée par la Graph API."""


class WritesDisabledError(RuntimeError):
    """Une écriture a été tentée alors que le mode écriture est désactivé."""


class MetaClient:
    """Encapsule les appels HTTP vers Meta.

    Le token n'est jamais écrit sur disque ni transmis ailleurs qu'à
    `graph.facebook.com` et `graph.threads.net`.
    """

    def __init__(
        self,
        user_token: str,
        threads_token: str | None = None,
        *,
        enable_writes: bool = False,
    ) -> None:
        self._user_token = user_token
        self._threads_token = threads_token
        self._enable_writes = enable_writes
        self._page_tokens: dict[str, str] = {}
        self._http = httpx.AsyncClient(timeout=TIMEOUT_SECONDS)

    async def __aenter__(self) -> MetaClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    @property
    def has_token(self) -> bool:
        return bool(self._user_token)

    @property
    def writes_enabled(self) -> bool:
        return self._enable_writes

    def require_token(self) -> None:
        if not self._user_token:
            raise GraphAPIError(_TOKEN_ERROR)

    def require_writes(self, action: str) -> None:
        """Bloque toute écriture si le mode écriture n'est pas activé."""
        if not self._enable_writes:
            raise WritesDisabledError(
                f"Écriture refusée ({action}) : le mode écriture est désactivé. "
                "Pour l'activer, mets META_ENABLE_WRITES=true dans ta config. "
                "Garde-fou volontaire pour éviter toute publication accidentelle."
            )

    @staticmethod
    def _raise_for_payload(response: httpx.Response) -> JSONDict:
        try:
            payload: JSONDict = response.json()
        except ValueError as exc:  # réponse non JSON
            raise GraphAPIError(
                f"Réponse non JSON ({response.status_code}): {response.text[:200]}"
            ) from exc
        if response.status_code >= 400 or "error" in payload:
            err = payload.get("error", {})
            raise GraphAPIError(
                f"{err.get('type', 'HTTPError')} {response.status_code}: "
                f"{err.get('message', response.text[:200])}"
            )
        return payload

    async def get(
        self,
        path: str,
        params: JSONDict | None = None,
        *,
        token: str | None = None,
        base: str = GRAPH_API_BASE,
    ) -> JSONDict:
        """GET sur la Graph API."""
        if token is None:
            self.require_token()
        query: JSONDict = {"access_token": token or self._user_token, **(params or {})}
        response = await self._http.get(f"{base}/{path.lstrip('/')}", params=query)
        return self._raise_for_payload(response)

    async def post(
        self,
        path: str,
        data: JSONDict | None = None,
        *,
        token: str | None = None,
        base: str = GRAPH_API_BASE,
    ) -> JSONDict:
        """POST sur la Graph API (création, publication)."""
        if token is None:
            self.require_token()
        body: dict[str, str] = {"access_token": token or self._user_token}
        for key, value in (data or {}).items():
            if value is None:
                continue
            body[key] = value if isinstance(value, str) else str(value)
        response = await self._http.post(f"{base}/{path.lstrip('/')}", data=body)
        return self._raise_for_payload(response)

    async def post_multipart(
        self,
        path: str,
        data: JSONDict | None = None,
        *,
        files: dict[str, tuple[str, bytes, str]],
        token: str | None = None,
        base: str = GRAPH_API_BASE,
        timeout: float | None = None,
    ) -> JSONDict:
        """POST multipart/form-data (upload de fichier binaire, ex. photo locale).

        `timeout` permet d'allonger le délai pour les gros fichiers (vidéo),
        le client étant configuré par défaut sur un délai court.
        """
        if token is None:
            self.require_token()
        body: dict[str, str] = {"access_token": token or self._user_token}
        for key, value in (data or {}).items():
            if value is None:
                continue
            body[key] = value if isinstance(value, str) else str(value)
        response = await self._http.post(
            f"{base}/{path.lstrip('/')}",
            data=body,
            files=files,
            timeout=timeout if timeout is not None else TIMEOUT_SECONDS,
        )
        return self._raise_for_payload(response)

    async def delete(
        self,
        path: str,
        params: JSONDict | None = None,
        *,
        token: str | None = None,
        base: str = GRAPH_API_BASE,
    ) -> JSONDict:
        """DELETE sur la Graph API."""
        if token is None:
            self.require_token()
        query: JSONDict = {"access_token": token or self._user_token, **(params or {})}
        response = await self._http.request(
            "DELETE", f"{base}/{path.lstrip('/')}", params=query
        )
        return self._raise_for_payload(response)

    async def paginate(
        self,
        path: str,
        params: JSONDict | None = None,
        *,
        token: str | None = None,
        max_pages: int = 10,
    ) -> list[JSONDict]:
        """Suit la pagination `paging.next` et agrège les résultats."""
        rows: list[JSONDict] = []
        page = await self.get(path, params, token=token)
        rows.extend(page.get("data", []))
        pages = 1
        while pages < max_pages:
            nxt = page.get("paging", {}).get("next")
            if not nxt:
                break
            response = await self._http.get(nxt)
            try:
                page = self._raise_for_payload(response)
            except GraphAPIError:
                break
            rows.extend(page.get("data", []))
            pages += 1
        return rows

    async def page_token(self, page_id: str) -> str:
        """Renvoie (et met en cache) le token d'une Page."""
        if page_id in self._page_tokens:
            return self._page_tokens[page_id]
        data = await self.get(page_id, {"fields": "access_token"})
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            # Repli : le token utilisateur suffit souvent.
            return self._user_token
        self._page_tokens[page_id] = token
        return token

    async def threads_get(self, path: str, params: JSONDict | None = None) -> JSONDict:
        """GET sur l'API Threads."""
        token = self._threads_token or self._user_token
        if not token:
            raise GraphAPIError(_TOKEN_ERROR)
        return await self.get(path, params, token=token, base=THREADS_API_BASE)

    async def threads_post(self, path: str, data: JSONDict | None = None) -> JSONDict:
        """POST sur l'API Threads."""
        token = self._threads_token or self._user_token
        if not token:
            raise GraphAPIError(_TOKEN_ERROR)
        return await self.post(path, data, token=token, base=THREADS_API_BASE)
