from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx


class GraphError(Exception):
    """A Microsoft Graph failure already mapped to a stable AccessPilot error code (see 13_ERROR_CONTRACT.md)."""

    def __init__(self, code: str, message: str, status_code: int, *, http_status: int | None = None):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.http_status = http_status
        super().__init__(message)


def _map_error(http_status: int) -> tuple[str, int]:
    if http_status == 401:
        return "PROVIDER_AUTHENTICATION_FAILED", 502
    if http_status == 403:
        return "PROVIDER_PERMISSION_DENIED", 502
    if http_status == 404:
        return "PROVIDER_RESOURCE_NOT_FOUND", 502
    if http_status == 409:
        return "PROVIDER_CONFLICT", 409
    if http_status == 429:
        return "GRAPH_THROTTLED", 429
    if 500 <= http_status < 600:
        return "PROVIDER_UNAVAILABLE", 503
    return "PROVIDER_UNAVAILABLE", 502


@dataclass(frozen=True)
class GraphCredentials:
    tenant_id: str
    client_id: str
    client_secret: str
    authority: str
    graph_base_url: str = "https://graph.microsoft.com/v1.0"


class GraphClient:
    """Minimal Microsoft Graph application-permission (client-credentials) client."""

    def __init__(self, credentials: GraphCredentials, *, http_client: httpx.AsyncClient | None = None):
        self._credentials = credentials
        self._http = http_client
        self._owns_http = http_client is None
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    async def __aenter__(self) -> "GraphClient":
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=15.0)
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()

    def _token_url(self) -> str:
        return f"{self._credentials.authority.rstrip('/')}/oauth2/v2.0/token"

    async def _get_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        assert self._http is not None
        data = {
            "grant_type": "client_credentials",
            "client_id": self._credentials.client_id,
            "client_secret": self._credentials.client_secret,
            "scope": "https://graph.microsoft.com/.default",
        }
        try:
            response = await self._http.post(self._token_url(), data=data)
        except httpx.TimeoutException as exc:
            raise GraphError("PROVIDER_TIMEOUT", "Timed out authenticating to Microsoft Entra.", 504) from exc
        except httpx.HTTPError as exc:
            raise GraphError("PROVIDER_UNAVAILABLE", "Could not reach the Microsoft Entra token endpoint.", 503) from exc
        if response.status_code != 200:
            raise GraphError("PROVIDER_AUTHENTICATION_FAILED", "Microsoft Graph authentication failed.", 502, http_status=response.status_code)
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise GraphError("PROVIDER_AUTHENTICATION_FAILED", "Microsoft Graph did not return an access token.", 502)
        self._token = token
        self._token_expires_at = time.monotonic() + max(int(payload.get("expires_in", 3600)) - 60, 30)
        return token

    async def request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> httpx.Response:
        assert self._http is not None
        token = await self._get_token()
        url = path if path.startswith("http") else f"{self._credentials.graph_base_url.rstrip('/')}{path}"
        request_headers = {"Authorization": f"Bearer {token}", **(headers or {})}
        try:
            response = await self._http.request(method, url, params=params, json=json, headers=request_headers)
        except httpx.TimeoutException as exc:
            raise GraphError("PROVIDER_TIMEOUT", "Microsoft Graph request timed out.", 504) from exc
        except httpx.HTTPError as exc:
            raise GraphError("PROVIDER_UNAVAILABLE", "Microsoft Graph could not be reached.", 503) from exc
        if response.status_code >= 400:
            code, status_code = _map_error(response.status_code)
            raise GraphError(code, f"Microsoft Graph request failed ({response.status_code}).", status_code, http_status=response.status_code)
        return response

    async def get_all(self, path: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_url: str | None = path
        next_params: dict[str, Any] | None = params
        while next_url:
            response = await self.request("GET", next_url, params=next_params, headers=headers)
            payload = response.json()
            items.extend(payload.get("value", []))
            next_url = payload.get("@odata.nextLink")
            next_params = None
        return items

    async def verify_authentication(self) -> None:
        """Acquires a token, raising GraphError if the configured credentials are invalid."""
        await self._get_token()

    async def get_one(self, path: str, *, headers: dict[str, str] | None = None) -> dict[str, Any] | None:
        try:
            response = await self.request("GET", path, headers=headers)
        except GraphError as exc:
            if exc.code == "PROVIDER_RESOURCE_NOT_FOUND":
                return None
            raise
        return response.json()
