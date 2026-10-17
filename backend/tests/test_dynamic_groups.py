from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AccessAssignment, DynamicGroup, DynamicGroupTarget, Group, IdentityProvider, User, UserGroup
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


async def _seed_provider_and_users(factory) -> dict:
    async with factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        disconnected = IdentityProvider(name="Broken", type="MOCK", status="ERROR", tenant_id="t2")
        session.add_all([provider, disconnected])
        await session.flush()
        sales = User(provider_id=provider.id, external_id="u-sales", email="sales@x.com", display_name="Sales Person", status="ACTIVE", department="Sales")
        eng = User(provider_id=provider.id, external_id="u-eng", email="eng@x.com", display_name="Eng Person", status="ACTIVE", department="Engineering")
        session.add_all([sales, eng])
        await session.commit()
        await session.refresh(sales)
        await session.refresh(eng)
        return {"provider_id": provider.id, "disconnected_provider_id": disconnected.id, "sales_id": sales.id, "eng_id": eng.id}


def _rule_sales_eq() -> dict:
    return {"rule": {"operator": "AND", "conditions": [{"field": "department", "operator": "EQUALS", "value": "Sales"}]}}


@pytest.mark.asyncio
async def test_creating_a_dynamic_group_auto_creates_one_target_per_connected_provider_only(db_override):
    seeded = await _seed_provider_and_users(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/dynamic-groups", json={"name": "Sales DG", **_rule_sales_eq()})
    assert response.status_code == 201
    body = response.json()
    assert len(body["targets"]) == 1  # the ERROR-status provider is excluded
    assert body["targets"][0]["provider_id"] == str(seeded["provider_id"])
    assert body["targets"][0]["sync_mode"] == "OFF"
    assert body["targets"][0]["group_id"] is None


@pytest.mark.asyncio
async def test_duplicate_dynamic_group_name_is_rejected(db_override):
    await _seed_provider_and_users(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/dynamic-groups", json={"name": "Sales DG", **_rule_sales_eq()})
        second = await client.post("/api/v1/dynamic-groups", json={"name": "Sales DG", **_rule_sales_eq()})
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_setting_a_target_to_eligible_creates_a_real_group_but_grants_land_eligible_only(db_override):
    seeded = await _seed_provider_and_users(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/dynamic-groups", json={"name": "Sales DG", **_rule_sales_eq()})
        dynamic_group_id = UUID(created.json()["id"])
        response = await client.post(f"/api/v1/dynamic-groups/{dynamic_group_id}/targets/{seeded['provider_id']}/sync", json={"mode": "ELIGIBLE"})
    assert response.status_code == 200
    target = response.json()
    assert target["sync_mode"] == "ELIGIBLE"
    assert target["group_id"] is not None

    async with db_override.factory() as session:
        group = await session.get(Group, UUID(target["group_id"]))
        assert group is not None and group.name == "Sales DG"
        assignment = (await session.execute(select(AccessAssignment).where(AccessAssignment.dynamic_group_id == dynamic_group_id, AccessAssignment.user_id == seeded["sales_id"]))).scalar_one()
        assert assignment.status == "ELIGIBLE"
        # not pushed real yet — no local membership row
        membership = (await session.execute(select(UserGroup).where(UserGroup.user_id == seeded["sales_id"], UserGroup.group_id == group.id))).scalars().first()
        assert membership is None
        eng_assignment = (await session.execute(select(AccessAssignment).where(AccessAssignment.dynamic_group_id == dynamic_group_id, AccessAssignment.user_id == seeded["eng_id"]))).scalars().first()
        assert eng_assignment is None  # Engineering never matched


@pytest.mark.asyncio
async def test_setting_a_target_to_active_pushes_real_membership_immediately(db_override):
    seeded = await _seed_provider_and_users(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/dynamic-groups", json={"name": "Sales DG", "group_label": "STANDARD", **_rule_sales_eq()})
        dynamic_group_id = UUID(created.json()["id"])
        response = await client.post(f"/api/v1/dynamic-groups/{dynamic_group_id}/targets/{seeded['provider_id']}/sync", json={"mode": "ACTIVE"})
    assert response.status_code == 200
    target = response.json()
    assert target["sync_mode"] == "ACTIVE"

    async with db_override.factory() as session:
        group = await session.get(Group, UUID(target["group_id"]))
        assert group.group_label == "STANDARD"  # propagated from the dynamic group
        assignment = (await session.execute(select(AccessAssignment).where(AccessAssignment.dynamic_group_id == dynamic_group_id, AccessAssignment.user_id == seeded["sales_id"]))).scalar_one()
        assert assignment.status == "ACTIVE"
        membership = (await session.execute(select(UserGroup).where(UserGroup.user_id == seeded["sales_id"], UserGroup.group_id == group.id))).scalars().first()
        assert membership is not None  # real push confirmed via local membership mirror


@pytest.mark.asyncio
async def test_switching_eligible_to_active_promotes_held_assignments(db_override):
    seeded = await _seed_provider_and_users(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/dynamic-groups", json={"name": "Sales DG", **_rule_sales_eq()})
        dynamic_group_id = UUID(created.json()["id"])
        await client.post(f"/api/v1/dynamic-groups/{dynamic_group_id}/targets/{seeded['provider_id']}/sync", json={"mode": "ELIGIBLE"})
        promoted = await client.post(f"/api/v1/dynamic-groups/{dynamic_group_id}/targets/{seeded['provider_id']}/sync", json={"mode": "ACTIVE"})
    assert promoted.status_code == 200
    async with db_override.factory() as session:
        assignment = (await session.execute(select(AccessAssignment).where(AccessAssignment.dynamic_group_id == dynamic_group_id, AccessAssignment.user_id == seeded["sales_id"]))).scalar_one()
        assert assignment.status == "ACTIVE"


@pytest.mark.asyncio
async def test_switching_active_to_eligible_demotes_without_losing_the_record(db_override):
    seeded = await _seed_provider_and_users(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/dynamic-groups", json={"name": "Sales DG", **_rule_sales_eq()})
        dynamic_group_id = UUID(created.json()["id"])
        await client.post(f"/api/v1/dynamic-groups/{dynamic_group_id}/targets/{seeded['provider_id']}/sync", json={"mode": "ACTIVE"})
        demoted = await client.post(f"/api/v1/dynamic-groups/{dynamic_group_id}/targets/{seeded['provider_id']}/sync", json={"mode": "ELIGIBLE"})
    assert demoted.status_code == 200
    async with db_override.factory() as session:
        assignment = (await session.execute(select(AccessAssignment).where(AccessAssignment.dynamic_group_id == dynamic_group_id, AccessAssignment.user_id == seeded["sales_id"]))).scalar_one()
        assert assignment.status == "ELIGIBLE"
        group_id = assignment.resource_id
        membership = (await session.execute(select(UserGroup).where(UserGroup.user_id == seeded["sales_id"], UserGroup.group_id == group_id))).scalars().first()
        assert membership is None  # real membership removed


@pytest.mark.asyncio
async def test_department_change_grants_and_later_revokes_real_membership(db_override):
    seeded = await _seed_provider_and_users(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/dynamic-groups", json={"name": "Sales DG", **_rule_sales_eq()})
        dynamic_group_id = UUID(created.json()["id"])
        await client.post(f"/api/v1/dynamic-groups/{dynamic_group_id}/targets/{seeded['provider_id']}/sync", json={"mode": "ACTIVE"})

    async with db_override.factory() as session:
        from app.services.dynamic_groups import evaluate_dynamic_groups_for_user
        eng = await session.get(User, seeded["eng_id"])
        eng.department = "Sales"
        await session.commit()
        await evaluate_dynamic_groups_for_user(session, seeded["eng_id"], "admin-oid", "req-dg-1")
        granted = (await session.execute(select(AccessAssignment).where(AccessAssignment.dynamic_group_id == dynamic_group_id, AccessAssignment.user_id == seeded["eng_id"]))).scalar_one()
        assert granted.status == "ACTIVE"

        eng = await session.get(User, seeded["eng_id"])
        eng.department = "Engineering"
        await session.commit()
        await evaluate_dynamic_groups_for_user(session, seeded["eng_id"], "admin-oid", "req-dg-2")
        await session.refresh(granted)
        assert granted.status == "REVOKED"


@pytest.mark.asyncio
async def test_turning_a_target_off_leaves_existing_real_membership_untouched(db_override):
    seeded = await _seed_provider_and_users(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/dynamic-groups", json={"name": "Sales DG", **_rule_sales_eq()})
        dynamic_group_id = UUID(created.json()["id"])
        await client.post(f"/api/v1/dynamic-groups/{dynamic_group_id}/targets/{seeded['provider_id']}/sync", json={"mode": "ACTIVE"})
        off = await client.post(f"/api/v1/dynamic-groups/{dynamic_group_id}/targets/{seeded['provider_id']}/sync", json={"mode": "OFF"})
    assert off.status_code == 200 and off.json()["sync_mode"] == "OFF"
    async with db_override.factory() as session:
        assignment = (await session.execute(select(AccessAssignment).where(AccessAssignment.dynamic_group_id == dynamic_group_id, AccessAssignment.user_id == seeded["sales_id"]))).scalar_one()
        assert assignment.status == "ACTIVE"  # untouched by turning sync off
        group_id = assignment.resource_id
        membership = (await session.execute(select(UserGroup).where(UserGroup.user_id == seeded["sales_id"], UserGroup.group_id == group_id))).scalars().first()
        assert membership is not None  # real membership still intact


@pytest.mark.asyncio
async def test_deleting_a_dynamic_group_does_not_touch_the_real_group(db_override):
    seeded = await _seed_provider_and_users(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/dynamic-groups", json={"name": "Sales DG", **_rule_sales_eq()})
        dynamic_group_id = UUID(created.json()["id"])
        synced = await client.post(f"/api/v1/dynamic-groups/{dynamic_group_id}/targets/{seeded['provider_id']}/sync", json={"mode": "ACTIVE"})
        group_id = UUID(synced.json()["group_id"])
        deleted = await client.delete(f"/api/v1/dynamic-groups/{dynamic_group_id}")
    assert deleted.status_code == 204
    async with db_override.factory() as session:
        assert await session.get(DynamicGroup, dynamic_group_id) is None
        assert await session.get(Group, group_id) is not None  # real group survives


@pytest.mark.asyncio
async def test_deleting_a_dynamic_group_with_a_revoked_assignment_history_does_not_fail(db_override):
    """Real bug caught live: deleting a dynamic group that ever made a grant — even one since fully revoked —
    hit a real foreign-key violation, because the historical AccessAssignment row still referenced it. Fixed by
    clearing dynamic_group_id on every assignment (kept as a real historical record) before the delete."""
    seeded = await _seed_provider_and_users(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/dynamic-groups", json={"name": "Sales DG", **_rule_sales_eq()})
        dynamic_group_id = UUID(created.json()["id"])
        await client.post(f"/api/v1/dynamic-groups/{dynamic_group_id}/targets/{seeded['provider_id']}/sync", json={"mode": "ACTIVE"})

    async with db_override.factory() as session:
        sales = await session.get(User, seeded["sales_id"])
        sales.department = "Engineering"  # no longer matches -> revoked, but history remains
        await session.commit()
        from app.services.dynamic_groups import evaluate_dynamic_groups_for_user
        await evaluate_dynamic_groups_for_user(session, seeded["sales_id"], "admin-oid", "req-revoke-first")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        deleted = await client.delete(f"/api/v1/dynamic-groups/{dynamic_group_id}")
    assert deleted.status_code == 204
    async with db_override.factory() as session:
        assert await session.get(DynamicGroup, dynamic_group_id) is None
        assignment = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == seeded["sales_id"]))).scalar_one()
        assert assignment.status == "REVOKED" and assignment.dynamic_group_id is None


@pytest.mark.asyncio
async def test_disabling_the_dynamic_group_revokes_what_it_granted(db_override):
    seeded = await _seed_provider_and_users(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/dynamic-groups", json={"name": "Sales DG", **_rule_sales_eq()})
        dynamic_group_id = UUID(created.json()["id"])
        await client.post(f"/api/v1/dynamic-groups/{dynamic_group_id}/targets/{seeded['provider_id']}/sync", json={"mode": "ACTIVE"})
        disabled = await client.patch(f"/api/v1/dynamic-groups/{dynamic_group_id}", json={"status": "DISABLED"})
    assert disabled.status_code == 200
    async with db_override.factory() as session:
        assignment = (await session.execute(select(AccessAssignment).where(AccessAssignment.dynamic_group_id == dynamic_group_id, AccessAssignment.user_id == seeded["sales_id"]))).scalar_one()
        assert assignment.status == "REVOKED"
