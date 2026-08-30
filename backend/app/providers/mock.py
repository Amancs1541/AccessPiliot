from __future__ import annotations

from typing import Any

from app.providers.base import CreatedUser, IdentityProvider, NewGroupRequest, NewUserRequest, NormalizedApplication, NormalizedApplicationRole, NormalizedDomain, NormalizedGroup, NormalizedRole, NormalizedUser, ProviderConflictError


class MockProvider(IdentityProvider):
    def __init__(self) -> None:
        self.users = [NormalizedUser("user-001", "jordan.lee@northstar.io", "Jordan Lee", "Jordan", "Lee", "Platform Engineering", "Senior Cloud Engineer"), NormalizedUser("user-002", "priya.nair@northstar.io", "Priya Nair", "Priya", "Nair", "Security", "Security Architect")]
        self.groups = [NormalizedGroup("group-001", "Platform Engineering", "Engineering delivery team"), NormalizedGroup("group-002", "Security Operations", "Security incident response", True)]
        self.roles = [NormalizedRole("role-001", "Production Administrator", "Elevated production operations", is_privileged=True), NormalizedRole("role-002", "Reports Reader", "Read usage and sign-in reports")]
        self.applications = [NormalizedApplication("app-001", "Reporting Portal", app_roles=(NormalizedApplicationRole("approle-001", "Viewer", "Read-only access"), NormalizedApplicationRole("approle-002", "Editor", "Read-write access")))]
        self.memberships: dict[str, set[str]] = {"group-001": {"user-001"}, "group-002": {"user-002"}}
        self.app_role_assignments: set[tuple[str, str, str]] = set()
        self.assignments: dict[str, str] = {}
        self.domains = [NormalizedDomain("northstar.io", is_verified=True, is_default=True), NormalizedDomain("northstar.onmicrosoft.com", is_verified=True)]

    async def test_connection(self) -> bool: return True
    async def get_users(self, query: str | None = None) -> list[NormalizedUser]: return self._filter(self.users, query, lambda item: f"{item.display_name} {item.email}")
    async def get_user(self, external_id: str) -> NormalizedUser | None: return next((item for item in self.users if item.external_id == external_id), None)
    async def get_groups(self, query: str | None = None) -> list[NormalizedGroup]: return self._filter(self.groups, query, lambda item: f"{item.name} {item.description or ''}")
    async def get_group(self, external_id: str) -> NormalizedGroup | None: return next((item for item in self.groups if item.external_id == external_id), None)
    async def get_group_members(self, external_id: str) -> list[NormalizedUser]: return [user for user in self.users if user.external_id in self.memberships.get(external_id, set())]
    async def add_group_member(self, group_external_id: str, user_external_id: str) -> bool: self.memberships.setdefault(group_external_id, set()).add(user_external_id); return True
    async def remove_group_member(self, group_external_id: str, user_external_id: str) -> bool: self.memberships.setdefault(group_external_id, set()).discard(user_external_id); return True
    async def get_roles(self, query: str | None = None) -> list[NormalizedRole]: return self._filter(self.roles, query, lambda item: f"{item.name} {item.description or ''}")
    async def get_role(self, external_id: str) -> NormalizedRole | None: return next((item for item in self.roles if item.external_id == external_id), None)
    async def get_role_assignments(self, external_role_id: str) -> list[dict[str, Any]]: return [{"user_external_id": user, "status": status} for user, status in self.assignments.items() if external_role_id]
    async def get_applications(self, query: str | None = None) -> list[NormalizedApplication]: return self._filter(self.applications, query, lambda item: item.name)
    async def activate_assignment(self, request: dict[str, Any]) -> bool:
        if request.get("resource_type") == "GROUP" and request.get("target_external_id") and request.get("user_external_id"):
            await self.add_group_member(request["target_external_id"], request["user_external_id"])
        if request.get("resource_type") == "APPLICATION" and request.get("target_external_id") and request.get("app_role_external_id") and request.get("user_external_id"):
            self.app_role_assignments.add((request["target_external_id"], request["app_role_external_id"], request["user_external_id"]))
        if "assignment_id" in request:
            self.assignments[str(request["assignment_id"])] = "ACTIVE"
        return True

    async def revoke_assignment(self, assignment: dict[str, Any]) -> bool:
        if assignment.get("resource_type") == "GROUP" and assignment.get("target_external_id") and assignment.get("user_external_id"):
            await self.remove_group_member(assignment["target_external_id"], assignment["user_external_id"])
        if assignment.get("resource_type") == "APPLICATION" and assignment.get("target_external_id") and assignment.get("app_role_external_id") and assignment.get("user_external_id"):
            self.app_role_assignments.discard((assignment["target_external_id"], assignment["app_role_external_id"], assignment["user_external_id"]))
        if "assignment_id" in assignment:
            self.assignments[str(assignment["assignment_id"])] = "REVOKED"
        return True
    async def extend_assignment(self, assignment: dict[str, Any], duration_minutes: int) -> bool: return assignment.get("status") == "ACTIVE" and duration_minutes > 0
    async def sync(self) -> dict[str, int]: return {"users": len(self.users), "groups": len(self.groups), "roles": len(self.roles), "errors": 0}

    async def create_user(self, request: NewUserRequest) -> CreatedUser:
        if any(user.email.lower() == request.user_principal_name.lower() for user in self.users):
            raise ProviderConflictError("A user with this email already exists.")
        user = NormalizedUser(f"user-{len(self.users) + 1:03d}", request.user_principal_name, request.display_name, department=request.department, job_title=request.job_title)
        self.users.append(user)
        return CreatedUser(user=user, temporary_password="Mock-Only-Password-1!")

    async def create_group(self, request: NewGroupRequest) -> NormalizedGroup:
        if any(group.name.lower() == request.display_name.lower() for group in self.groups):
            raise ProviderConflictError("A group with this name already exists.")
        group = NormalizedGroup(f"group-{len(self.groups) + 1:03d}", request.display_name, request.description)
        self.groups.append(group)
        self.memberships.setdefault(group.external_id, set())
        return group

    async def get_domains(self) -> list[NormalizedDomain]:
        return list(self.domains)

    @staticmethod
    def _filter(items: list[Any], query: str | None, text: Any) -> list[Any]:
        if not query: return items.copy()
        query = query.lower()
        return [item for item in items if query in text(item).lower()]
