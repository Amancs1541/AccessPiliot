from __future__ import annotations

import secrets
from typing import Any

import httpx

from app.core.config import get_settings
from app.providers.base import CreatedUser, IdentityProvider, NewGroupRequest, NewUserRequest, NormalizedApplication, NormalizedApplicationRole, NormalizedGroup, NormalizedRole, NormalizedUser, ProviderConflictError
from app.providers.graph_client import GraphClient, GraphCredentials, GraphError
from app.security.credential_encryption import CredentialEncryptionError, decrypt_credential
from app.security.secrets import SecretReferenceStore

USER_SELECT = "id,userPrincipalName,mail,displayName,givenName,surname,department,jobTitle,accountEnabled"
GROUP_SELECT = "id,displayName,description,securityEnabled,isAssignableToRole"
ROLE_SELECT = "id,displayName,description"
APPLICATION_SELECT = "id,displayName,accountEnabled,appRoles"
DEFAULT_APP_ROLE_ID = "00000000-0000-0000-0000-000000000000"


def _odata_escape(value: str) -> str:
    return value.replace("'", "''")


def _is_already_assigned_error(exc: GraphError) -> bool:
    """Microsoft Graph reports a duplicate group/role member or app role assignment as HTTP 400
    (not 409) with a message like "...already exist...". Treat that as an idempotent success."""
    return exc.http_status == 400 and bool(exc.graph_message) and "already exist" in exc.graph_message.lower()


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

    @staticmethod
    def _application_from_graph(item: dict[str, Any]) -> NormalizedApplication:
        app_roles = [NormalizedApplicationRole(external_id=role["id"], name=role.get("displayName") or "Unnamed role", description=role.get("description")) for role in item.get("appRoles", []) if role.get("isEnabled", True)]
        if not app_roles:
            app_roles = [NormalizedApplicationRole(external_id=DEFAULT_APP_ROLE_ID, name="Default Access", description="Basic access with no application-defined role.")]
        return NormalizedApplication(external_id=item["id"], name=item.get("displayName") or "", status="ACTIVE" if item.get("accountEnabled", True) else "DISABLED", app_roles=tuple(app_roles))

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

    async def get_user_licenses(self, external_id: str) -> list[dict[str, str]]:
        """Best-effort live read of a user's assigned Microsoft 365/Entra licenses — not synced/stored, fetched
        on demand. Resolving human-readable SKU names needs Organization.Read.All; if that's not granted, the
        raw SKU id is used as the name instead of failing the whole lookup."""
        async with self._client() as client:
            item = await client.get_one(f"/users/{external_id}?$select=assignedLicenses")
            sku_ids = [entry["skuId"] for entry in (item or {}).get("assignedLicenses", []) if entry.get("skuId")]
            if not sku_ids:
                return []
            names = {sku_id: sku_id for sku_id in sku_ids}
            try:
                skus = await client.get_all("/subscribedSkus")
                names.update({sku["skuId"]: (sku.get("skuPartNumber") or sku["skuId"]) for sku in skus})
            except GraphError:
                pass
        return [{"sku_id": sku_id, "name": names[sku_id]} for sku_id in sku_ids]

    async def get_user_app_role_assignments(self, external_id: str) -> list[dict[str, str]]:
        """Live read of ALL of a user's application role assignments — including ones granted directly in Entra
        (outside AccessPilot). Not synced/stored; only needs AppRoleAssignment.ReadWrite.All, already granted."""
        async with self._client() as client:
            items = await client.get_all(f"/users/{external_id}/appRoleAssignments")
        return [
            {"resource_id": item["resourceId"], "resource_display_name": item.get("resourceDisplayName") or item["resourceId"], "app_role_id": item.get("appRoleId") or ""}
            for item in items if item.get("resourceId")
        ]

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

    async def add_group_member(self, group_external_id: str, user_external_id: str) -> bool:
        async with self._client() as client:
            try:
                await client.request("POST", f"/groups/{group_external_id}/members/$ref", json={"@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{user_external_id}"})
            except GraphError as exc:
                if exc.code == "PROVIDER_CONFLICT" or _is_already_assigned_error(exc):
                    return True
                raise
        return True

    async def remove_group_member(self, group_external_id: str, user_external_id: str) -> bool:
        async with self._client() as client:
            try:
                await client.request("DELETE", f"/groups/{group_external_id}/members/{user_external_id}/$ref")
            except GraphError as exc:
                if exc.code == "PROVIDER_RESOURCE_NOT_FOUND":
                    return True
                raise
        return True

    async def _add_role_member(self, role_external_id: str, user_external_id: str) -> bool:
        async with self._client() as client:
            try:
                await client.request("POST", f"/directoryRoles/{role_external_id}/members/$ref", json={"@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{user_external_id}"})
            except GraphError as exc:
                if exc.code == "PROVIDER_CONFLICT" or _is_already_assigned_error(exc):
                    return True
                raise
        return True

    async def _remove_role_member(self, role_external_id: str, user_external_id: str) -> bool:
        async with self._client() as client:
            try:
                await client.request("DELETE", f"/directoryRoles/{role_external_id}/members/{user_external_id}/$ref")
            except GraphError as exc:
                if exc.code == "PROVIDER_RESOURCE_NOT_FOUND":
                    return True
                raise
        return True

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

    async def get_applications(self, query: str | None = None) -> list[NormalizedApplication]:
        params: dict[str, Any] = {"$select": APPLICATION_SELECT, "$top": "999"}
        headers: dict[str, str] | None = None
        if query:
            escaped = _odata_escape(query)
            params["$filter"] = f"startswith(displayName,'{escaped}')"
            headers = {"ConsistencyLevel": "eventual"}
            params["$count"] = "true"
        async with self._client() as client:
            items = await client.get_all("/servicePrincipals", params=params, headers=headers)
        return [self._application_from_graph(item) for item in items]

    async def _add_app_role_assignment(self, resource_external_id: str, app_role_external_id: str, user_external_id: str) -> bool:
        async with self._client() as client:
            try:
                await client.request("POST", f"/users/{user_external_id}/appRoleAssignments", json={"principalId": user_external_id, "resourceId": resource_external_id, "appRoleId": app_role_external_id})
            except GraphError as exc:
                if exc.code == "PROVIDER_CONFLICT" or _is_already_assigned_error(exc):
                    return True
                raise
        return True

    async def _remove_app_role_assignment(self, resource_external_id: str, app_role_external_id: str, user_external_id: str) -> bool:
        async with self._client() as client:
            assignments = await client.get_all(f"/users/{user_external_id}/appRoleAssignments")
            match = next((item for item in assignments if item.get("resourceId") == resource_external_id and item.get("appRoleId") == app_role_external_id), None)
            if match is None:
                return True
            try:
                await client.request("DELETE", f"/users/{user_external_id}/appRoleAssignments/{match['id']}")
            except GraphError as exc:
                if exc.code == "PROVIDER_RESOURCE_NOT_FOUND":
                    return True
                raise
        return True

    async def activate_assignment(self, request: dict[str, Any]) -> bool:
        resource_type = request.get("resource_type")
        target_external_id = request.get("target_external_id")
        user_external_id = request.get("user_external_id")
        if not resource_type or not target_external_id or not user_external_id:
            raise GraphError("VALIDATION_ERROR", "Assignment activation requires resource_type, target_external_id, and user_external_id.", 400)
        if resource_type == "GROUP":
            return await self.add_group_member(target_external_id, user_external_id)
        if resource_type == "ROLE":
            return await self._add_role_member(target_external_id, user_external_id)
        if resource_type == "APPLICATION":
            app_role_external_id = request.get("app_role_external_id")
            if not app_role_external_id:
                raise GraphError("VALIDATION_ERROR", "Application assignment activation requires app_role_external_id.", 400)
            return await self._add_app_role_assignment(target_external_id, app_role_external_id, user_external_id)
        raise GraphError("VALIDATION_ERROR", f"Unsupported assignment resource type: {resource_type}", 400)

    async def revoke_assignment(self, assignment: dict[str, Any]) -> bool:
        resource_type = assignment.get("resource_type")
        target_external_id = assignment.get("target_external_id")
        user_external_id = assignment.get("user_external_id")
        if not resource_type or not target_external_id or not user_external_id:
            raise GraphError("VALIDATION_ERROR", "Assignment revocation requires resource_type, target_external_id, and user_external_id.", 400)
        if resource_type == "GROUP":
            return await self.remove_group_member(target_external_id, user_external_id)
        if resource_type == "ROLE":
            return await self._remove_role_member(target_external_id, user_external_id)
        if resource_type == "APPLICATION":
            app_role_external_id = assignment.get("app_role_external_id")
            if not app_role_external_id:
                raise GraphError("VALIDATION_ERROR", "Application assignment revocation requires app_role_external_id.", 400)
            return await self._remove_app_role_assignment(target_external_id, app_role_external_id, user_external_id)
        raise GraphError("VALIDATION_ERROR", f"Unsupported assignment resource type: {resource_type}", 400)

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
        # Graph's POST /users response does NOT include `department`/`jobTitle` unless explicitly $select'd — they
        # ARE saved on the real object (we just set them above), just not echoed back. Trust what we sent rather
        # than what came back, or department-driven birthright policies would silently never match a freshly
        # provisioned user despite the real Entra object being correct.
        created = self._user_from_graph(response.json())
        normalized = NormalizedUser(external_id=created.external_id, email=created.email, display_name=created.display_name, given_name=created.given_name, surname=created.surname, department=request.department or created.department, job_title=request.job_title or created.job_title, status=created.status)
        return CreatedUser(user=normalized, temporary_password=password)

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
