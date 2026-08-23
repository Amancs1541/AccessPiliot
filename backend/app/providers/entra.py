from __future__ import annotations

import secrets
from typing import Any

import httpx

from app.core.config import get_settings
from app.providers.base import CreatedUser, IdentityProvider, NewGroupRequest, NewUserRequest, NormalizedGroup, NormalizedRole, NormalizedUser, ProviderConflictError
from app.providers.graph_client import GraphClient, GraphCredentials, GraphError
from app.security.credential_encryption import CredentialEncryptionError, decrypt_credential
from app.security.secrets import SecretReferenceStore

USER_SELECT = "id,userPrincipalName,mail,displayName,givenName,surname,department,jobTitle,accountEnabled"
GROUP_SELECT = "id,displayName,description,securityEnabled,isAssignableToRole"
ROLE_SELECT = "id,displayName,description"


def _odata_escape(value: str) -> str:
    return value.replace("'", "''")


class EntraProvider(IdentityProvider):
    """Entra connector backed by Microsoft Graph application permissions."""

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
        if not (tenant_id in issuer and bool(metadata.get("jwks_uri"))):
            return False
        if self._resolve_secret() is None:
            return True
        try:
            async with self._client() as client:
                await client.verify_authentication()
        except GraphError:
            return False
        return True

    def _resolve_secret(self) -> str | None:
        settings = get_settings()
        encrypted = getattr(self.provider, "graph_client_secret_encrypted", None)
        if encrypted:
            try:
                return decrypt_credential(encrypted)
            except CredentialEncryptionError as exc:
                raise GraphError("PROVIDER_AUTHENTICATION_FAILED", str(exc), 502) from exc
        configuration_ref = getattr(self.provider, "configuration_ref", None)
        if configuration_ref and configuration_ref != "DATABASE_ENCRYPTED":
            resolved = SecretReferenceStore().resolve(configuration_ref)
            if resolved:
                return resolved
        return settings.entra_api_client_secret or None

    def _credentials(self) -> GraphCredentials:
        settings = get_settings()
        tenant_id = getattr(self.provider, "tenant_id", None)
        client_id = getattr(self.provider, "graph_client_id", None) or getattr(self.provider, "client_id", None)
        authority = getattr(self.provider, "authority", None)
        if not tenant_id or not client_id or not authority:
            raise GraphError("PROVIDER_AUTHENTICATION_FAILED", "The Entra connector is missing tenant, client, or authority configuration.", 502)
        secret = self._resolve_secret()
        if not secret:
            raise GraphError("PROVIDER_AUTHENTICATION_FAILED", "The Microsoft Graph client secret is not configured.", 502)
        return GraphCredentials(tenant_id=tenant_id, client_id=client_id, client_secret=secret, authority=authority, graph_base_url=settings.graph_base_url)

    def _client(self) -> GraphClient:
        return GraphClient(self._credentials())

    @staticmethod
    def _user_from_graph(item: dict[str, Any]) -> NormalizedUser:
        return NormalizedUser(
            external_id=item["id"],
            email=item.get("mail") or item.get("userPrincipalName") or "",
            display_name=item.get("displayName") or "",
            given_name=item.get("givenName"),
            surname=item.get("surname"),
            department=item.get("department"),
            job_title=item.get("jobTitle"),
            status="ACTIVE" if item.get("accountEnabled", True) else "DISABLED",
        )

    @staticmethod
    def _group_from_graph(item: dict[str, Any]) -> NormalizedGroup:
        return NormalizedGroup(external_id=item["id"], name=item.get("displayName") or "", description=item.get("description"), is_privileged=bool(item.get("isAssignableToRole")), status="ACTIVE")

    @staticmethod
    def _role_from_graph(item: dict[str, Any]) -> NormalizedRole:
        name = item.get("displayName") or ""
        return NormalizedRole(external_id=item["id"], name=name, description=item.get("description"), role_type="DIRECTORY_ROLE", is_privileged="administrator" in name.lower())

    async def get_users(self, query: str | None = None) -> list[NormalizedUser]:
        params: dict[str, Any] = {"$select": USER_SELECT, "$top": "999"}
        headers: dict[str, str] | None = None
        if query:
            escaped = _odata_escape(query)
            params["$filter"] = f"startswith(displayName,'{escaped}') or startswith(mail,'{escaped}') or startswith(userPrincipalName,'{escaped}')"
            headers = {"ConsistencyLevel": "eventual"}
            params["$count"] = "true"
        async with self._client() as client:
            items = await client.get_all("/users", params=params, headers=headers)
        return [self._user_from_graph(item) for item in items]

    async def get_user(self, external_id: str) -> NormalizedUser | None:
        async with self._client() as client:
            item = await client.get_one(f"/users/{external_id}?$select={USER_SELECT}")
        return self._user_from_graph(item) if item else None

    async def get_groups(self, query: str | None = None) -> list[NormalizedGroup]:
        params: dict[str, Any] = {"$select": GROUP_SELECT, "$top": "999"}
        headers: dict[str, str] | None = None
        if query:
            escaped = _odata_escape(query)
            params["$filter"] = f"startswith(displayName,'{escaped}')"
            headers = {"ConsistencyLevel": "eventual"}
            params["$count"] = "true"
        async with self._client() as client:
            items = await client.get_all("/groups", params=params, headers=headers)
        return [self._group_from_graph(item) for item in items]

    async def get_group(self, external_id: str) -> NormalizedGroup | None:
        async with self._client() as client:
            item = await client.get_one(f"/groups/{external_id}?$select={GROUP_SELECT}")
        return self._group_from_graph(item) if item else None

    async def get_group_members(self, external_id: str) -> list[NormalizedUser]:
        params = {"$select": USER_SELECT, "$top": "999"}
        async with self._client() as client:
            items = await client.get_all(f"/groups/{external_id}/members", params=params)
        return [self._user_from_graph(item) for item in items if item.get("@odata.type", "#microsoft.graph.user") == "#microsoft.graph.user"]

    async def add_group_member(self, group_external_id: str, user_external_id: str) -> bool: return await self._not_implemented()
    async def remove_group_member(self, group_external_id: str, user_external_id: str) -> bool: return await self._not_implemented()

    async def get_roles(self, query: str | None = None) -> list[NormalizedRole]:
        async with self._client() as client:
            items = await client.get_all("/directoryRoles", params={"$select": ROLE_SELECT})
        roles = [self._role_from_graph(item) for item in items]
        if query:
            needle = query.lower()
            roles = [role for role in roles if needle in role.name.lower()]
        return roles

    async def get_role(self, external_id: str) -> NormalizedRole | None:
        async with self._client() as client:
            item = await client.get_one(f"/directoryRoles/{external_id}?$select={ROLE_SELECT}")
        return self._role_from_graph(item) if item else None

    async def get_role_assignments(self, external_role_id: str) -> list[dict[str, Any]]: return await self._not_implemented()
    async def activate_assignment(self, request: dict[str, Any]) -> bool: return await self._not_implemented()
    async def revoke_assignment(self, assignment: dict[str, Any]) -> bool: return await self._not_implemented()
    async def extend_assignment(self, assignment: dict[str, Any], duration_minutes: int) -> bool: return await self._not_implemented()

    async def sync(self) -> dict[str, int]:
        # Orchestration (DB upserts) lives in app.services.directory_sync; the connector only fetches normalized data.
        return await self._not_implemented()

    async def create_user(self, request: NewUserRequest) -> CreatedUser:
        async with self._client() as client:
            existing = await client.get_all("/users", params={"$filter": f"userPrincipalName eq '{_odata_escape(request.user_principal_name)}'", "$select": USER_SELECT, "$count": "true"}, headers={"ConsistencyLevel": "eventual"})
            if existing:
                raise ProviderConflictError("A user with this email already exists in Microsoft Entra.")
            password = secrets.token_urlsafe(18)
            body: dict[str, Any] = {
                "accountEnabled": True,
                "displayName": request.display_name,
                "mailNickname": request.mail_nickname,
                "userPrincipalName": request.user_principal_name,
                "passwordProfile": {"forceChangePasswordNextSignIn": True, "password": password},
            }
            if request.department:
                body["department"] = request.department
            if request.job_title:
                body["jobTitle"] = request.job_title
            response = await client.request("POST", "/users", json=body)
        return CreatedUser(user=self._user_from_graph(response.json()), temporary_password=password)

    async def create_group(self, request: NewGroupRequest) -> NormalizedGroup:
        async with self._client() as client:
            existing = await client.get_all("/groups", params={"$filter": f"displayName eq '{_odata_escape(request.display_name)}'", "$select": GROUP_SELECT, "$count": "true"}, headers={"ConsistencyLevel": "eventual"})
            if existing:
                raise ProviderConflictError("A group with this name already exists in Microsoft Entra.")
            nickname = request.mail_nickname or "".join(character for character in request.display_name if character.isalnum()) or "group"
            body: dict[str, Any] = {"displayName": request.display_name, "mailEnabled": False, "mailNickname": nickname, "securityEnabled": True}
            if request.description:
                body["description"] = request.description
            response = await client.request("POST", "/groups", json=body)
        return self._group_from_graph(response.json())

    async def _not_implemented(self): raise NotImplementedError("This Entra IAM operation is deferred to a later phase")
