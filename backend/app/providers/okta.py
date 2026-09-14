from __future__ import annotations

import secrets
from typing import Any

import httpx

from app.providers.base import CreatedUser, IdentityProvider, NewGroupRequest, NewUserRequest, NormalizedApplication, NormalizedApplicationPermission, NormalizedApplicationRole, NormalizedDomain, NormalizedGroup, NormalizedRole, NormalizedUser, ProviderConflictError
from app.providers.graph_client import GraphError
from app.providers.okta_client import OktaClient
from app.security.credential_encryption import CredentialEncryptionError, decrypt_credential

DEFAULT_APP_ROLE_ID = "00000000-0000-0000-0000-000000000000"

# Okta has no API to enumerate "all possible admin role types" the way Entra's /directoryRoles does — standard
# admin roles are a fixed, product-defined catalog (see Okta's own Roles documentation), not a queryable resource.
STANDARD_ROLES: tuple[tuple[str, str], ...] = (
    ("SUPER_ADMIN", "Super Administrator"),
    ("ORG_ADMIN", "Organizational Administrator"),
    ("APP_ADMIN", "Application Administrator"),
    ("USER_ADMIN", "Group Administrator"),
    ("HELP_DESK_ADMIN", "Help Desk Administrator"),
    ("READ_ONLY_ADMIN", "Read-Only Administrator"),
    ("MOBILE_ADMIN", "Mobile Administrator"),
    ("API_ACCESS_MANAGEMENT_ADMIN", "API Access Management Administrator"),
    ("REPORT_ADMIN", "Report Administrator"),
    ("GROUP_MEMBERSHIP_ADMIN", "Group Membership Administrator"),
)


def _display_name(profile: dict[str, Any]) -> str:
    first, last = profile.get("firstName") or "", profile.get("lastName") or ""
    combined = f"{first} {last}".strip()
    return combined or profile.get("login") or ""


class OktaProvider(IdentityProvider):
    """Okta connector authenticated with a static SSWS API token. This class only ever runs once an admin
    explicitly creates an IdentityProvider row with type="OKTA" and configures its organization_url + API
    token — app.services.provider_configuration._connector() is the single dispatch point that decides which
    connector class handles a given provider row, and it only instantiates OktaProvider for that provider type.
    Until that configuration exists, this file changes no behavior for any existing ENTRA/MOCK provider.

    UNVERIFIED AGAINST A LIVE OKTA ORG: this implementation follows Okta's public API docs, but several details
    (exact conflict status codes on duplicate role/app assignment, the shape of app "roles") should be confirmed
    the first time a real Okta org is actually connected — see the NHI management plan's phasing notes.
    """

    def __init__(self, provider: Any = None):
        self.provider = provider

    def _org_url(self) -> str:
        org_url = getattr(self.provider, "organization_url", None)
        if not org_url:
            raise ValueError("An Okta organization URL is required")
        return org_url

    def _resolve_token(self) -> str | None:
        encrypted = getattr(self.provider, "graph_client_secret_encrypted", None)
        if not encrypted:
            return None
        try:
            return decrypt_credential(encrypted)
        except CredentialEncryptionError as exc:
            raise GraphError("PROVIDER_AUTHENTICATION_FAILED", str(exc), 502) from exc

    def _client(self) -> OktaClient:
        token = self._resolve_token()
        if not token:
            raise GraphError("PROVIDER_AUTHENTICATION_FAILED", "The Okta API token is not configured.", 502)
        return OktaClient(self._org_url(), token)

    async def test_connection(self) -> bool:
        org_url = self._org_url()
        metadata_url = f"{org_url.rstrip('/')}/.well-known/okta-organization"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(metadata_url)
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise TimeoutError("Okta organization metadata request timed out") from exc
        except httpx.HTTPError as exc:
            raise ConnectionError("Okta organization metadata request failed") from exc
        if self._resolve_token() is None:
            return True
        try:
            async with self._client() as client:
                await client.verify_authentication()
        except GraphError:
            return False
        return True

    @staticmethod
    def _user_from_okta(item: dict[str, Any]) -> NormalizedUser:
        profile = item.get("profile") or {}
        return NormalizedUser(
            external_id=item["id"],
            email=profile.get("email") or profile.get("login") or "",
            display_name=_display_name(profile),
            given_name=profile.get("firstName"),
            surname=profile.get("lastName"),
            department=profile.get("department"),
            job_title=profile.get("title"),
            status="ACTIVE" if item.get("status") == "ACTIVE" else "DISABLED",
        )

    @staticmethod
    def _group_from_okta(item: dict[str, Any]) -> NormalizedGroup:
        profile = item.get("profile") or {}
        return NormalizedGroup(external_id=item["id"], name=profile.get("name") or "", description=profile.get("description"), is_privileged=False, status="ACTIVE")

    @staticmethod
    def _application_from_okta(item: dict[str, Any]) -> NormalizedApplication:
        # Okta app instances don't expose an "app roles" catalog the way Entra service principals do — fall back
        # to the same single default-access role Entra uses when a resource has none of its own, until a real
        # Okta tenant clarifies what finer-grained role model (if any) this needs.
        default_role = NormalizedApplicationRole(external_id=DEFAULT_APP_ROLE_ID, name="Default Access", description="Basic access with no application-defined role.")
        # credential_expires_at is left None: Okta's /apps list response doesn't expose credential/secret expiry
        # the way Entra's passwordCredentials/keyCredentials do — tracking that would need a per-app follow-up
        # call this first pass doesn't make, to avoid an N+1 request per app during sync.
        return NormalizedApplication(external_id=item["id"], name=item.get("label") or item.get("name") or "", status="ACTIVE" if item.get("status") == "ACTIVE" else "DISABLED", app_roles=(default_role,), nhi_type="OKTA_SERVICE_APP", credential_expires_at=None)

    async def get_users(self, query: str | None = None) -> list[NormalizedUser]:
        params: dict[str, Any] = {"limit": 200}
        if query:
            params["q"] = query
        async with self._client() as client:
            items = await client.get_all("/users", params=params)
        return [self._user_from_okta(item) for item in items]

    async def get_user(self, external_id: str) -> NormalizedUser | None:
        async with self._client() as client:
            item = await client.get_one(f"/users/{external_id}")
        return self._user_from_okta(item) if item else None

    async def update_user(self, external_id: str, *, department: str | None, job_title: str | None) -> NormalizedUser:
        """POST (not PATCH) is Okta's real partial-profile-update verb — a full PUT would silently wipe every
        other profile field this app never touches."""
        async with self._client() as client:
            response = await client.request("POST", f"/users/{external_id}", json={"profile": {"department": department, "title": job_title}})
        return self._user_from_okta(response.json())

    async def get_groups(self, query: str | None = None) -> list[NormalizedGroup]:
        params: dict[str, Any] = {"limit": 200}
        if query:
            params["q"] = query
        async with self._client() as client:
            items = await client.get_all("/groups", params=params)
        return [self._group_from_okta(item) for item in items]

    async def get_group(self, external_id: str) -> NormalizedGroup | None:
        async with self._client() as client:
            item = await client.get_one(f"/groups/{external_id}")
        return self._group_from_okta(item) if item else None

    async def get_group_members(self, external_id: str) -> list[NormalizedUser]:
        async with self._client() as client:
            items = await client.get_all(f"/groups/{external_id}/users", params={"limit": 200})
        return [self._user_from_okta(item) for item in items]

    async def add_group_member(self, group_external_id: str, user_external_id: str) -> bool:
        async with self._client() as client:
            await client.request("PUT", f"/groups/{group_external_id}/users/{user_external_id}")
        return True

    async def remove_group_member(self, group_external_id: str, user_external_id: str) -> bool:
        async with self._client() as client:
            try:
                await client.request("DELETE", f"/groups/{group_external_id}/users/{user_external_id}")
            except GraphError as exc:
                if exc.code == "PROVIDER_RESOURCE_NOT_FOUND":
                    return True
                raise
        return True

    async def get_roles(self, query: str | None = None) -> list[NormalizedRole]:
        roles = [NormalizedRole(external_id=role_type, name=name, description=None, role_type="DIRECTORY_ROLE", is_privileged=True) for role_type, name in STANDARD_ROLES]
        if query:
            needle = query.lower()
            roles = [role for role in roles if needle in role.name.lower()]
        return roles

    async def get_role(self, external_id: str) -> NormalizedRole | None:
        return next((role for role in await self.get_roles() if role.external_id == external_id), None)

    async def get_role_assignments(self, external_role_id: str) -> list[dict[str, Any]]:
        return await self._not_implemented()

    async def _add_role_member(self, role_external_id: str, user_external_id: str) -> bool:
        async with self._client() as client:
            try:
                await client.request("POST", f"/users/{user_external_id}/roles", json={"type": role_external_id})
            except GraphError as exc:
                if exc.code == "PROVIDER_CONFLICT":
                    return True
                raise
        return True

    async def _remove_role_member(self, role_external_id: str, user_external_id: str) -> bool:
        async with self._client() as client:
            assignments = await client.get_all(f"/users/{user_external_id}/roles")
            match = next((item for item in assignments if item.get("type") == role_external_id), None)
            if match is None:
                return True
            try:
                await client.request("DELETE", f"/users/{user_external_id}/roles/{match['id']}")
            except GraphError as exc:
                if exc.code == "PROVIDER_RESOURCE_NOT_FOUND":
                    return True
                raise
        return True

    async def get_applications(self, query: str | None = None) -> list[NormalizedApplication]:
        params: dict[str, Any] = {"limit": 200}
        if query:
            params["q"] = query
        async with self._client() as client:
            items = await client.get_all("/apps", params=params)
        return [self._application_from_okta(item) for item in items]

    async def set_application_enabled(self, external_id: str, enabled: bool) -> bool:
        """Okta's app lifecycle endpoints, not a PATCH — real, documented, and symmetric with
        EntraProvider.set_application_enabled so the NHI detail page's Enable/Disable action works identically
        regardless of which provider the identity actually came from."""
        async with self._client() as client:
            await client.request("POST", f"/apps/{external_id}/lifecycle/{'activate' if enabled else 'deactivate'}")
        return True

    async def get_application_permissions(self, external_id: str) -> list[NormalizedApplicationPermission]:
        """Okta's OAuth grants for this app (the scopes it's been consented to call on another API) — the closest
        Okta equivalent to Entra's appRoleAssignments. UNVERIFIED against a live org: /apps/{id}/grants is a real,
        documented Okta endpoint, but its exact response shape hasn't been checked against a real Okta tenant."""
        async with self._client() as client:
            items = await client.get_all(f"/apps/{external_id}/grants")
        return [
            NormalizedApplicationPermission(resource_external_id=item.get("issuer", ""), resource_display_name=item.get("issuer") or "Okta authorization server", role_name=item.get("scopeId") or "Unknown scope")
            for item in items
        ]

    async def _add_app_assignment(self, app_external_id: str, user_external_id: str) -> bool:
        async with self._client() as client:
            try:
                await client.request("POST", f"/apps/{app_external_id}/users", json={"id": user_external_id, "scope": "USER"})
            except GraphError as exc:
                if exc.code == "PROVIDER_CONFLICT":
                    return True
                raise
        return True

    async def _remove_app_assignment(self, app_external_id: str, user_external_id: str) -> bool:
        async with self._client() as client:
            try:
                await client.request("DELETE", f"/apps/{app_external_id}/users/{user_external_id}")
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
            return await self._add_app_assignment(target_external_id, user_external_id)
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
            return await self._remove_app_assignment(target_external_id, user_external_id)
        raise GraphError("VALIDATION_ERROR", f"Unsupported assignment resource type: {resource_type}", 400)

    async def extend_assignment(self, assignment: dict[str, Any], duration_minutes: int) -> bool:
        return await self._not_implemented()

    async def sync(self) -> dict[str, int]:
        # Orchestration (DB upserts) lives in app.services.directory_sync; the connector only fetches normalized
        # data — same division of responsibility as EntraProvider.sync().
        return await self._not_implemented()

    async def create_user(self, request: NewUserRequest) -> CreatedUser:
        async with self._client() as client:
            existing = await client.get_all("/users", params={"filter": f'profile.login eq "{request.user_principal_name}"'})
            if existing:
                raise ProviderConflictError("A user with this login already exists in Okta.")
            password = secrets.token_urlsafe(18)
            first_name, _, last_name = request.display_name.partition(" ")
            profile: dict[str, Any] = {"firstName": first_name, "lastName": last_name, "email": request.user_principal_name, "login": request.user_principal_name}
            if request.department:
                profile["department"] = request.department
            if request.job_title:
                profile["title"] = request.job_title
            response = await client.request("POST", "/users", params={"activate": "true"}, json={"profile": profile, "credentials": {"password": {"value": password}}})
        created = self._user_from_okta(response.json())
        normalized = NormalizedUser(external_id=created.external_id, email=created.email, display_name=created.display_name, given_name=created.given_name, surname=created.surname, department=request.department or created.department, job_title=request.job_title or created.job_title, status=created.status)
        return CreatedUser(user=normalized, temporary_password=password)

    async def create_group(self, request: NewGroupRequest) -> NormalizedGroup:
        async with self._client() as client:
            existing = await client.get_all("/groups", params={"q": request.display_name})
            if any((item.get("profile") or {}).get("name") == request.display_name for item in existing):
                raise ProviderConflictError("A group with this name already exists in Okta.")
            profile: dict[str, Any] = {"name": request.display_name}
            if request.description:
                profile["description"] = request.description
            response = await client.request("POST", "/groups", json={"profile": profile})
        return self._group_from_okta(response.json())

    async def get_domains(self) -> list[NormalizedDomain]:
        async with self._client() as client:
            response = await client.request("GET", "/domains")
        domains = response.json().get("domains", [])
        return [NormalizedDomain(name=item["domain"], is_verified=item.get("validationStatus") == "VERIFIED", is_default=False) for item in domains if item.get("domain")]

    async def _not_implemented(self):
        raise NotImplementedError("This Okta IAM operation is deferred to a later phase")
