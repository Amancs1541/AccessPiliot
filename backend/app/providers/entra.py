from __future__ import annotations

from typing import Any

import httpx

from app.providers.base import IdentityProvider, NormalizedGroup, NormalizedRole, NormalizedUser


class EntraProvider(IdentityProvider):
    """Entra connector limited to authentication metadata validation in Phase 3."""

    def __init__(self, provider: Any = None):
        self.provider = provider

    async def test_connection(self) -> bool:
        authority = getattr(self.provider, "authority", None)
        tenant_id = getattr(self.provider, "tenant_id", None)
        if not authority or not tenant_id:
            raise ValueError("Entra authority and tenant ID are required")
        metadata_url = f"{str(authority).rstrip('/')}/v2.0/.well-known/openid-configuration"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(metadata_url)
                response.raise_for_status()
                metadata = response.json()
        except httpx.TimeoutException as exc:
            raise TimeoutError("Entra metadata request timed out") from exc
        except httpx.HTTPError as exc:
            raise ConnectionError("Entra metadata request failed") from exc
        issuer = metadata.get("issuer", "")
        return tenant_id in issuer and bool(metadata.get("jwks_uri"))

    async def _not_implemented(self): raise NotImplementedError("Entra IAM operations are deferred to a later phase")
    async def get_users(self, query: str | None = None) -> list[NormalizedUser]: return await self._not_implemented()
    async def get_user(self, external_id: str) -> NormalizedUser | None: return await self._not_implemented()
    async def get_groups(self, query: str | None = None) -> list[NormalizedGroup]: return await self._not_implemented()
    async def get_group(self, external_id: str) -> NormalizedGroup | None: return await self._not_implemented()
    async def get_group_members(self, external_id: str) -> list[NormalizedUser]: return await self._not_implemented()
    async def add_group_member(self, group_external_id: str, user_external_id: str) -> bool: return await self._not_implemented()
    async def remove_group_member(self, group_external_id: str, user_external_id: str) -> bool: return await self._not_implemented()
    async def get_roles(self, query: str | None = None) -> list[NormalizedRole]: return await self._not_implemented()
    async def get_role(self, external_id: str) -> NormalizedRole | None: return await self._not_implemented()
    async def get_role_assignments(self, external_role_id: str) -> list[dict[str, Any]]: return await self._not_implemented()
    async def activate_assignment(self, request: dict[str, Any]) -> bool: return await self._not_implemented()
    async def revoke_assignment(self, assignment: dict[str, Any]) -> bool: return await self._not_implemented()
    async def extend_assignment(self, assignment: dict[str, Any], duration_minutes: int) -> bool: return await self._not_implemented()
    async def sync(self) -> dict[str, int]: return await self._not_implemented()
