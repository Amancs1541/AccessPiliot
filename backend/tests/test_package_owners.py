from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AccessAssignment, AccessPackage, AccessPackageAssignment, AccessPackageItem, AccessPackageOwner, Group, IdentityProvider, Role, User
from app.security.auth import AuthenticatedUser, require_authenticated_user


class TestSession:
    __test__ = False

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


def future(hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


async def _seed(factory) -> dict:
    async with factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        group = Group(provider_id=provider.id, external_id="g-own", name="Owned Group", status="ACTIVE", is_privileged=False)
        role = Role(provider_id=provider.id, external_id="r-own", name="Owned Role", role_type="DIRECTORY_ROLE", status="ACTIVE")
        owner = User(provider_id=provider.id, external_id="owner-oid", email="owner@x.com", display_name="Pat Owner", status="ACTIVE")
        other = User(provider_id=provider.id, external_id="other-oid", email="other@x.com", display_name="Other Person", status="ACTIVE")
        session.add_all([group, role, owner, other])
        await session.commit()
        return {"provider_id": provider.id, "group_id": group.id, "role_id": role.id, "owner_id": owner.id, "other_id": other.id}


async def _create_package(client, seeded, owner_ids=None):
    body = {"name": "Starter Kit", "items": [{"resource_type": "GROUP", "resource_id": str(seeded["group_id"])}, {"resource_type": "ROLE", "resource_id": str(seeded["role_id"])}]}
    if owner_ids is not None:
        body["owner_ids"] = [str(i) for i in owner_ids]
    return await client.post("/api/v1/packages", json=body)


@pytest.mark.asyncio
async def test_admin_can_set_owners_when_creating_a_package(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await _create_package(client, seeded, [seeded["owner_id"]])
    assert response.status_code == 201
    assert [o["display_name"] for o in response.json()["owners"]] == ["Pat Owner"]


@pytest.mark.asyncio
async def test_owner_can_rename_and_remove_an_item_but_not_the_last_one(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (await _create_package(client, seeded, [seeded["owner_id"]])).json()
        authenticate_as("AccessPilot.User", subject="owner-oid")
        owned = await client.get("/api/v1/packages/owned")
        assert [p["name"] for p in owned.json()] == ["Starter Kit"]

        renamed = await client.patch(f"/api/v1/packages/{created['id']}/owner-rename", json={"name": "Renamed Kit"})
        assert renamed.status_code == 200 and renamed.json()["name"] == "Renamed Kit"

        first_item = created["items"][0]["id"]
        removed = await client.delete(f"/api/v1/packages/{created['id']}/items/{first_item}")
        assert removed.status_code == 200 and len(removed.json()["items"]) == 1

        last_item = removed.json()["items"][0]["id"]
        blocked = await client.delete(f"/api/v1/packages/{created['id']}/items/{last_item}")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "PACKAGE_MUST_HAVE_ITEM"


@pytest.mark.asyncio
async def test_a_non_owner_cannot_rename_or_remove_items(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (await _create_package(client, seeded, [seeded["owner_id"]])).json()
        authenticate_as("AccessPilot.User", subject="other-oid")
        rename = await client.patch(f"/api/v1/packages/{created['id']}/owner-rename", json={"name": "Hijacked"})
        remove = await client.delete(f"/api/v1/packages/{created['id']}/items/{created['items'][0]['id']}")
        owned = await client.get("/api/v1/packages/owned")
    assert rename.status_code == 403
    assert remove.status_code == 403
    assert owned.json() == []


@pytest.mark.asyncio
async def test_owner_cannot_use_the_admin_package_endpoints(db_override):
    """An owner's portal is deliberately narrow — the general edit/delete/assign endpoints stay permission-gated."""
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (await _create_package(client, seeded, [seeded["owner_id"]])).json()
        authenticate_as("AccessPilot.User", subject="owner-oid")
        edit = await client.patch(f"/api/v1/packages/{created['id']}", json={"name": "Direct edit"})
        delete = await client.delete(f"/api/v1/packages/{created['id']}")
    assert edit.status_code == 403
    assert delete.status_code == 403


@pytest.mark.asyncio
async def test_package_owner_is_suggested_as_reviewer_and_items_carry_their_package(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (await _create_package(client, seeded, [seeded["owner_id"]])).json()
        suggestions = await client.get(f"/api/v1/access-reviews/owner-suggestions?resource_type=PACKAGE&resource_id={created['id']}")
    assert suggestions.status_code == 200
    assert suggestions.json()[0]["display_name"] == "Pat Owner"
    assert suggestions.json()[0]["source"] == "Package owner"

    async with db_override.factory() as session:
        assignment = AccessAssignment(provider_id=seeded["provider_id"], user_id=seeded["other_id"], resource_type="GROUP", resource_id=seeded["group_id"], assignment_type="PERMANENT", status="ACTIVE", justification="Package: Starter Kit")
        session.add(assignment)
        await session.flush()
        session.add(AccessPackageAssignment(package_id=UUID(created["id"]), package_assignment_id=uuid4(), assignment_id=assignment.id, user_id=seeded["other_id"]))
        await session.commit()

    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        campaign = await client.post("/api/v1/access-reviews", json={"name": "Pkg review", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "PACKAGE", "scope_resource_id": created["id"], "reviewer_id": str(seeded["owner_id"]), "due_at": future(24)})
        items = (await client.get(f"/api/v1/access-reviews/{campaign.json()['id']}/items")).json()
    assert len(items) == 1
    assert items[0]["package_id"] == created["id"]
    assert items[0]["package_name"] == "Starter Kit"
