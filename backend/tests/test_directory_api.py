import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import Group, IdentityProvider, Role, User, UserGroup
from app.providers.base import CreatedUser, NormalizedGroup, NormalizedUser, ProviderConflictError
from app.security.auth import AuthenticatedUser, require_authenticated_user


class TestSession:
    def __init__(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_override():
    database = TestSession()
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async def override():
        async with database.factory() as session:
            yield session
    app.dependency_overrides[get_db] = override
    yield database
    app.dependency_overrides.clear()
    await database.engine.dispose()


def as_role(role: str):
    async def dependency():
        return AuthenticatedUser("actor-1", "Actor", "actor@example.com", "tenant", (role,), {})
    return dependency


def authenticate_as(role: str) -> None:
    app.dependency_overrides[require_authenticated_user] = as_role(role)


@pytest.mark.asyncio
async def test_users_groups_roles_empty_state_for_admin(db_override):
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        users_response = await client.get("/api/v1/users")
        groups_response = await client.get("/api/v1/groups")
        roles_response = await client.get("/api/v1/roles")
    assert users_response.status_code == 200 and users_response.json() == []
    assert groups_response.status_code == 200 and groups_response.json() == []
    assert roles_response.status_code == 200 and roles_response.json() == []


@pytest.mark.asyncio
async def test_normal_user_cannot_read_directory(db_override):
    authenticate_as("AccessPilot.User")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/users")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCESS_DENIED"


@pytest.mark.asyncio
async def test_users_and_groups_reflect_seeded_data(db_override):
    async with db_override.factory() as session:
        provider = IdentityProvider(name="Entra", type="ENTRA", status="CONNECTED", tenant_id="tenant-1")
        session.add(provider)
        await session.flush()
        seeded_user = User(provider_id=provider.id, external_id="u1", email="a@b.com", display_name="A B", status="ACTIVE")
        seeded_group = Group(provider_id=provider.id, external_id="g1", name="Group One", status="ACTIVE", is_privileged=False)
        session.add_all([seeded_user, seeded_group])
        await session.flush()
        session.add(UserGroup(user_id=seeded_user.id, group_id=seeded_group.id, source="SYNC"))
        await session.commit()

    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        users_response = await client.get("/api/v1/users")
        groups_response = await client.get("/api/v1/groups")
        members_response = await client.get(f"/api/v1/groups/{groups_response.json()[0]['id']}/members")
    assert len(users_response.json()) == 1 and users_response.json()[0]["display_name"] == "A B"
    assert len(groups_response.json()) == 1
    assert len(members_response.json()) == 1


@pytest.mark.asyncio
async def test_create_user_success_and_duplicate(db_override, monkeypatch):
    async with db_override.factory() as session:
        session.add(IdentityProvider(name="Entra", type="ENTRA", status="CONNECTED", tenant_id="tenant-1"))
        await session.commit()

    authenticate_as("AccessPilot.Admin")
    calls = {"count": 0}

    async def fake_create_user(self, request):
        calls["count"] += 1
        if calls["count"] > 1:
            raise ProviderConflictError("A user with this email already exists in Microsoft Entra.")
        return CreatedUser(user=NormalizedUser(external_id="new-1", email=request.user_principal_name, display_name=request.display_name), temporary_password="Temp-Pass-1!")

    monkeypatch.setattr("app.providers.entra.EntraProvider.create_user", fake_create_user)

    payload = {"display_name": "New User", "user_principal_name": "new.user@tenant.onmicrosoft.com"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/users", json=payload)
        second = await client.post("/api/v1/users", json=payload)
    assert first.status_code == 201
    assert first.json()["temporary_password"] == "Temp-Pass-1!"
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "USER_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_create_user_denied_for_normal_user(db_override):
    authenticate_as("AccessPilot.User")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/users", json={"display_name": "X", "user_principal_name": "x@y.com"})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_create_group_success_and_duplicate(db_override, monkeypatch):
    async with db_override.factory() as session:
        session.add(IdentityProvider(name="Entra", type="ENTRA", status="CONNECTED", tenant_id="tenant-1"))
        await session.commit()

    authenticate_as("AccessPilot.Admin")
    calls = {"count": 0}

    async def fake_create_group(self, request):
        calls["count"] += 1
        if calls["count"] > 1:
            raise ProviderConflictError("A group with this name already exists in Microsoft Entra.")
        return NormalizedGroup(external_id="new-group-1", name=request.display_name, description=request.description)

    monkeypatch.setattr("app.providers.entra.EntraProvider.create_group", fake_create_group)

    payload = {"display_name": "New Group"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/groups", json=payload)
        second = await client.post("/api/v1/groups", json=payload)
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "GROUP_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_create_user_without_provider_returns_not_found(db_override):
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/users", json={"display_name": "X", "user_principal_name": "x@y.com"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PROVIDER_NOT_FOUND"


@pytest.mark.asyncio
async def test_dashboard_admin_returns_real_counts(db_override):
    async with db_override.factory() as session:
        provider = IdentityProvider(name="Entra", type="ENTRA", status="CONNECTED", tenant_id="tenant-1")
        session.add(provider)
        await session.flush()
        session.add(User(provider_id=provider.id, external_id="u1", email="a@b.com", display_name="A", status="ACTIVE"))
        session.add(Group(provider_id=provider.id, external_id="g1", name="G", status="ACTIVE", is_privileged=False))
        session.add(Role(provider_id=provider.id, external_id="r1", name="R", role_type="DIRECTORY_ROLE", status="ACTIVE", is_privileged=False))
        await session.commit()

    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/dashboard/admin")
    assert response.status_code == 200
    body = response.json()
    assert body["users"] == 1 and body["groups"] == 1 and body["roles"] == 1
    assert body["provider"]["status"] == "CONNECTED"


@pytest.mark.asyncio
async def test_dashboard_admin_denied_for_normal_user(db_override):
    authenticate_as("AccessPilot.User")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/dashboard/admin")
    assert response.status_code == 403
