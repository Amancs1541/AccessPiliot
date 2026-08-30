from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NormalizedUser:
    external_id: str
    email: str
    display_name: str
    given_name: str | None = None
    surname: str | None = None
    department: str | None = None
    job_title: str | None = None
    status: str = "ACTIVE"


@dataclass(frozen=True)
class NormalizedGroup:
    external_id: str
    name: str
    description: str | None = None
    is_privileged: bool = False
    status: str = "ACTIVE"


@dataclass(frozen=True)
class NormalizedRole:
    external_id: str
    name: str
    description: str | None = None
    role_type: str = "DIRECTORY_ROLE"
    is_privileged: bool = False
    status: str = "ACTIVE"


@dataclass(frozen=True)
class NormalizedApplicationRole:
    external_id: str
    name: str
    description: str | None = None


@dataclass(frozen=True)
class NormalizedApplication:
    external_id: str
    name: str
    status: str = "ACTIVE"
    app_roles: tuple[NormalizedApplicationRole, ...] = ()


@dataclass(frozen=True)
class NormalizedDomain:
    name: str
    is_verified: bool = False
    is_default: bool = False


class ProviderConflictError(Exception):
    """Raised by a connector when a create operation targets a resource that already exists."""


@dataclass(frozen=True)
class NewUserRequest:
    display_name: str
    user_principal_name: str
    mail_nickname: str
    department: str | None = None
    job_title: str | None = None


@dataclass(frozen=True)
class NewGroupRequest:
    display_name: str
    description: str | None = None
    mail_nickname: str | None = None


@dataclass(frozen=True)
class CreatedUser:
    user: NormalizedUser
    temporary_password: str | None = None


class IdentityProvider(ABC):
    @abstractmethod
    async def test_connection(self) -> bool: ...

    @abstractmethod
    async def get_users(self, query: str | None = None) -> list[NormalizedUser]: ...

    @abstractmethod
    async def get_user(self, external_id: str) -> NormalizedUser | None: ...

    @abstractmethod
    async def get_groups(self, query: str | None = None) -> list[NormalizedGroup]: ...

    @abstractmethod
    async def get_group(self, external_id: str) -> NormalizedGroup | None: ...

    @abstractmethod
    async def get_group_members(self, external_id: str) -> list[NormalizedUser]: ...

    @abstractmethod
    async def add_group_member(self, group_external_id: str, user_external_id: str) -> bool: ...

    @abstractmethod
    async def remove_group_member(self, group_external_id: str, user_external_id: str) -> bool: ...

    @abstractmethod
    async def get_roles(self, query: str | None = None) -> list[NormalizedRole]: ...

    @abstractmethod
    async def get_role(self, external_id: str) -> NormalizedRole | None: ...

    @abstractmethod
    async def get_role_assignments(self, external_role_id: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_applications(self, query: str | None = None) -> list[NormalizedApplication]: ...

    @abstractmethod
    async def activate_assignment(self, request: dict[str, Any]) -> bool: ...

    @abstractmethod
    async def revoke_assignment(self, assignment: dict[str, Any]) -> bool: ...

    @abstractmethod
    async def extend_assignment(self, assignment: dict[str, Any], duration_minutes: int) -> bool: ...

    @abstractmethod
    async def sync(self) -> dict[str, int]: ...

    @abstractmethod
    async def create_user(self, request: NewUserRequest) -> CreatedUser: ...

    @abstractmethod
    async def create_group(self, request: NewGroupRequest) -> NormalizedGroup: ...

    @abstractmethod
    async def get_domains(self) -> list[NormalizedDomain]:
        """The tenant's registered domains, so an Admin can pick a KNOWN VERIFIED one to provision new accounts
        into instead of trusting an arbitrary email domain from a CSV row (which the target IdP may reject)."""
        ...
