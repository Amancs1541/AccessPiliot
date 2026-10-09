import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import Application, EntitlementCatalogEntry, Group, IdentityProvider, Role, User
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


def authenticate_as(role: str, subject: str = "admin-oid") -> None:
    async def dependency():
        return AuthenticatedUser(subject, "Admin", "admin@example.com", "tenant", (role,), {})
    app.dependency_overrides[require_authenticated_user] = dependency


async def _seed_directory(factory):
    async with factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        group = Group(provider_id=provider.id, external_id="g-1", name="Finance Approvers", is_privileged=False, status="ACTIVE")
        role = Role(provider_id=provider.id, external_id="r-1", name="Global Reader", role_type="ENTRA_ROLE", is_privileged=False, status="ACTIVE")
        application = Application(
            provider_id=provider.id, external_id="app-1", name="Expense Tool", status="ACTIVE",
            app_roles=[{"id": "role-a", "name": "Approver"}, {"id": "role-b", "name": "Submitter"}],
        )
        owner = User(provider_id=provider.id, external_id="owner-1", email="owner@x.com", display_name="Jane Owner", status="ACTIVE")
        session.add_all([group, role, application, owner])
        await session.commit()
        return {"group_id": group.id, "role_id": role.id, "application_id": application.id, "owner_id": owner.id}


@pytest.mark.asyncio
async def test_listing_the_catalog_creates_a_blank_entry_for_every_real_entitlement(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/entitlement-catalog")
    assert response.status_code == 200
    entries = response.json()
    # 1 group + 1 role + 2 app roles (Expense Tool has no whole-app entry since it has AppRoles) = 4
    assert len(entries) == 4
    names = {e["resource_display_name"] for e in entries}
    assert names == {"Finance Approvers", "Global Reader", "Expense Tool — Approver", "Expense Tool — Submitter"}
    for entry in entries:
        assert entry["risk_tier"] == "LOW"
        assert entry["description"] is None
        assert entry["owner_id"] is None

    async with db_override.factory() as session:
        from sqlalchemy import select
        rows = (await session.execute(select(EntitlementCatalogEntry))).scalars().all()
    assert len(rows) == 4


@pytest.mark.asyncio
async def test_listing_twice_does_not_duplicate_entries(db_override):
    await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/api/v1/entitlement-catalog")
        second = await client.get("/api/v1/entitlement-catalog")
    assert len(second.json()) == 4


@pytest.mark.asyncio
async def test_admin_can_set_description_risk_tier_and_owner(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/v1/entitlement-catalog")
        group_entry = next(e for e in listed.json() if e["resource_display_name"] == "Finance Approvers")
        updated = await client.patch(f"/api/v1/entitlement-catalog/{group_entry['id']}", json={
            "description": "Grants PO approval up to $50k",
            "risk_tier": "HIGH",
            "owner_id": str(ids["owner_id"]),
        })
    assert updated.status_code == 200
    body = updated.json()
    assert body["description"] == "Grants PO approval up to $50k"
    assert body["risk_tier"] == "HIGH"
    assert body["owner_id"] == str(ids["owner_id"])
    assert body["owner_display_name"] == "Jane Owner"
    assert body["resource_display_name"] == "Finance Approvers"


@pytest.mark.asyncio
async def test_an_invalid_risk_tier_is_rejected(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/v1/entitlement-catalog")
        entry_id = listed.json()[0]["id"]
        response = await client.patch(f"/api/v1/entitlement-catalog/{entry_id}", json={"risk_tier": "SUPER_HIGH"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_updating_a_nonexistent_entry_404s(db_override):
    await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch("/api/v1/entitlement-catalog/00000000-0000-0000-0000-000000000000", json={"risk_tier": "HIGH"})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_normal_user_cannot_read_or_manage_the_catalog(db_override):
    await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.User", subject="regular-user-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/v1/entitlement-catalog")
        patched = await client.patch("/api/v1/entitlement-catalog/00000000-0000-0000-0000-000000000000", json={"risk_tier": "HIGH"})
    assert listed.status_code == 403
    assert patched.status_code == 403
