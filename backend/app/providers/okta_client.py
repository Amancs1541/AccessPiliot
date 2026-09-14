from __future__ import annotations

from typing import Any

import httpx

from app.providers.graph_client import GraphError, _map_error


class OktaClient:
    """Minimal Okta API client authenticated with a static SSWS API token — a simpler auth model than Microsoft
    Graph's OAuth2 client-credentials flow (GraphClient), so there's no token-exchange step here. Reuses
    GraphError/_map_error from graph_client.py so every existing catch site in the app (directory_sync.run_sync,
    provider_configuration.test_provider, etc.) already handles Okta failures identically to Entra ones, with
    zero changes to that code."""

    def __init__(self, org_url: str, api_token: str, *, http_client: httpx.AsyncClient | None = None):
        self._base_url = org_url.rstrip("/")
        self._token = api_token
        self._http = http_client
        self._owns_http = http_client is None

    async def __aenter__(self) -> "OktaClient":
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=15.0)
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()

    def _url(self, path: str) -> str:
        return path if path.startswith("http") else f"{self._base_url}/api/v1{path}"

    async def request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json: dict[str, Any] | None = None) -> httpx.Response:
        assert self._http is not None
        headers = {"Authorization": f"SSWS {self._token}", "Accept": "application/json"}
        try:
            response = await self._http.request(method, self._url(path), params=params, json=json, headers=headers)
        except httpx.TimeoutException as exc:
            raise GraphError("PROVIDER_TIMEOUT", "Okta request timed out.", 504) from exc
        except httpx.HTTPError as exc:
            raise GraphError("PROVIDER_UNAVAILABLE", "Okta could not be reached.", 503) from exc
        if response.status_code >= 400:
            code, status_code = _map_error(response.status_code)
            okta_message = None
            try:
                okta_message = response.json().get("errorSummary")
            except ValueError:
                pass
            raise GraphError(code, f"Okta request failed ({response.status_code}).", status_code, http_status=response.status_code, graph_message=okta_message)
        return response

    async def get_all(self, path: str, *, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Okta paginates via an RFC 5988 Link header (rel="next"), not an @odata.nextLink body field like Graph."""
        items: list[dict[str, Any]] = []
        next_url: str | None = path
        next_params: dict[str, Any] | None = params
        while next_url:
            response = await self.request("GET", next_url, params=next_params)
            items.extend(response.json())
            next_url = response.links.get("next", {}).get("url")
            next_params = None
        return items

    async def get_one(self, path: str) -> dict[str, Any] | None:
        try:
            response = await self.request("GET", path)
        except GraphError as exc:
            if exc.code == "PROVIDER_RESOURCE_NOT_FOUND":
                return None
            raise
        return response.json()

    async def verify_authentication(self) -> None:
        await self.request("GET", "/users", params={"limit": 1})
