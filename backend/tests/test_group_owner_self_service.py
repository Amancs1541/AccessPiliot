import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import Group, GroupOwner, IdentityProvider, User
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
        return AuthenticatedUser(subject, "Someone", "someone@example.com", "tenant", (role,), {})
    app.dependency_overrides[require_authenticated_user] = dependency


async def _seed(factory, with_owner: bool = True) -> dict:
    async with factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        group = Group(provider_id=provider.id, external_id="g-own", name="Finance Approvers", status="ACTIVE", is_privileged=False, description="Old description")
        owner = User(provider_id=provider.id, external_id="owner-oid", email="owner@x.com", display_name="Pat Owner", status="ACTIVE")
        other = User(provider_id=provider.id, external_id="other-oid", email="other@x.com", display_name="Other Person", status="ACTIVE")
        session.add_all([group, owner, other])
        await session.flush()
        if with_owner:
            session.add(GroupOwner(group_id=group.id, user_id=owner.id))
        await session.commit()
        return {"group_id": group.id, "owner_id": owner.id, "other_id": other.id}


@pytest.mark.asyncio
async def test_owner_can_edit_description_and_label_but_group_name_never_changes(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.User", subject="owner-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        owned = await client.get("/api/v1/groups/owned")
        assert [g["name"] for g in owned.json()] == ["Finance Approvers"]

        updated = await client.patch(f"/api/v1/groups/{seeded['group_id']}/owner-update", json={"description": "Grants PO approval up to $50k", "group_label": "Finance-Sensitive"})
    assert updated.status_code == 200
    body = updated.json()
    assert body["description"] == "Grants PO approval up to $50k"
    assert body["group_label"] == "Finance-Sensitive"
    assert body["name"] == "Finance Approvers"  # synced from the directory, never owner-editable


@pytest.mark.asyncio
async def test_a_non_owner_cannot_edit_the_group_or_see_it_in_owned(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.User", subject="other-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        owned = await client.get("/api/v1/groups/owned")
        updated = await client.patch(f"/api/v1/groups/{seeded['group_id']}/owner-update", json={"description": "Hijacked"})
    assert owned.json() == []
    assert updated.status_code == 403


@pytest.mark.asyncio
async def test_owner_cannot_use_the_admin_group_management_endpoints(db_override):
    """An owner's portal is deliberately narrow — the general group-owner-roster endpoint stays GROUP_MANAGE-gated."""
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.User", subject="owner-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        set_owners = await client.put(f"/api/v1/groups/{seeded['group_id']}/owners", json={"user_ids": [str(seeded["other_id"])]})
    assert set_owners.status_code == 403


@pytest.mark.asyncio
async def test_owner_update_is_partial_leaving_unset_fields_untouched(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.User", subject="owner-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        updated = await client.patch(f"/api/v1/groups/{seeded['group_id']}/owner-update", json={"group_label": "Finance-Sensitive"})
    assert updated.status_code == 200
    assert updated.json()["description"] == "Old description"  # untouched, since it wasn't sent
    assert updated.json()["group_label"] == "Finance-Sensitive"


@pytest.mark.asyncio
async def test_updating_a_nonexistent_group_404s(db_override):
    await _seed(db_override.factory)
    authenticate_as("AccessPilot.User", subject="owner-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        updated = await client.patch("/api/v1/groups/00000000-0000-0000-0000-000000000000/owner-update", json={"description": "x"})
    assert updated.status_code == 404
