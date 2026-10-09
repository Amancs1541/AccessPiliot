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
from app.models import IdentityProvider, User, WorkflowStageInstance
from app.security.auth import AuthenticatedUser, require_authenticated_user
from app.services.workflows import sweep_workflow_escalations


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


async def _seed(session) -> dict:
    provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
    session.add(provider)
    await session.flush()
    users = {
        "admin": User(provider_id=provider.id, external_id="admin-oid", email="admin@x.com", display_name="Admin", status="ACTIVE"),
        "approver_a": User(provider_id=provider.id, external_id="approver-a", email="a@x.com", display_name="Approver A", status="ACTIVE"),
        "approver_b": User(provider_id=provider.id, external_id="approver-b", email="b@x.com", display_name="Approver B", status="ACTIVE"),
        "fallback_x": User(provider_id=provider.id, external_id="fallback-x", email="x@x.com", display_name="Fallback X", status="ACTIVE"),
        "requester": User(provider_id=provider.id, external_id="requester-oid", email="r@x.com", display_name="Requester", status="ACTIVE"),
        "outsider": User(provider_id=provider.id, external_id="outsider-oid", email="o@x.com", display_name="Outsider", status="ACTIVE"),
    }
    session.add_all(users.values())
    await session.commit()
    for key, user in users.items():
        await session.refresh(user)
    return {"provider_id": provider.id, **{k: v.id for k, v in users.items()}}


def _simple_definition_payload(ids, approval_mode="ANY_OF", with_fallback=False):
    stage = {"name": "Manager approval", "approval_mode": approval_mode, "approver_user_ids": [str(ids["approver_a"]), str(ids["approver_b"])] if approval_mode == "ALL_OF" else [str(ids["approver_a"])]}
    if with_fallback:
        stage["fallback_approver_ids"] = [str(ids["fallback_x"])]
        stage["escalate_after_hours"] = 24
    return {"name": "Simple Workflow", "description": "A simple one-stage workflow.", "stages": [stage]}


@pytest.mark.asyncio
async def test_admin_can_create_a_workflow_definition_with_stages(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/workflows/definitions", json=_simple_definition_payload(ids))
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "DRAFT"
    assert len(body["stages"]) == 1
    assert body["stages"][0]["approvers"][0]["display_name"] == "Approver A"


@pytest.mark.asyncio
async def test_duplicate_definition_name_is_rejected(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/workflows/definitions", json=_simple_definition_payload(ids))
        dup = await client.post("/api/v1/workflows/definitions", json=_simple_definition_payload(ids))
    assert dup.status_code == 409


@pytest.mark.asyncio
async def test_stage_referencing_an_unknown_approver_is_rejected(db_override):
    async with db_override.factory() as session:
        await _seed(session)
    authenticate_as("AccessPilot.Admin")
    payload = {"name": "Bad Workflow", "stages": [{"name": "Stage 1", "approval_mode": "ANY_OF", "approver_user_ids": [str(uuid4())]}]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/workflows/definitions", json=payload)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_fallback_and_escalate_hours_must_be_set_together(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    missing_hours = {"name": "WF1", "stages": [{"name": "S1", "approver_user_ids": [str(ids["approver_a"])], "fallback_approver_ids": [str(ids["fallback_x"])]}]}
    missing_fallback = {"name": "WF2", "stages": [{"name": "S1", "approver_user_ids": [str(ids["approver_a"])], "escalate_after_hours": 24}]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/api/v1/workflows/definitions", json=missing_hours)
        r2 = await client.post("/api/v1/workflows/definitions", json=missing_fallback)
    assert r1.status_code == 422 and r2.status_code == 422


@pytest.mark.asyncio
async def test_condition_requires_all_three_parts_together(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    payload = {"name": "WF3", "stages": [{"name": "S1", "approver_user_ids": [str(ids["approver_a"])], "condition_field": "department"}]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/workflows/definitions", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_submitting_a_request_against_a_non_active_definition_is_rejected(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (await client.post("/api/v1/workflows/definitions", json=_simple_definition_payload(ids))).json()
        authenticate_as("AccessPilot.User", subject="requester-oid")
        response = await client.post("/api/v1/workflows/requests", json={"workflow_definition_id": created["id"], "title": "Need something", "justification": "Because reasons"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "WORKFLOW_DEFINITION_NOT_ACTIVE"


@pytest.mark.asyncio
async def test_any_of_stage_finalizes_on_first_decision(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (await client.post("/api/v1/workflows/definitions", json=_simple_definition_payload(ids))).json()
        await client.patch(f"/api/v1/workflows/definitions/{created['id']}", json={"status": "ACTIVE"})
        authenticate_as("AccessPilot.User", subject="requester-oid")
        submitted = (await client.post("/api/v1/workflows/requests", json={"workflow_definition_id": created["id"], "title": "Need something", "justification": "Because reasons"})).json()
        assert submitted["instance_status"] == "PENDING"
        stage_id = submitted["stages"][0]["id"]

        authenticate_as("AccessPilot.User", subject="approver-a")
        decided = await client.post(f"/api/v1/workflows/requests/{submitted['id']}/stages/{stage_id}/decide", json={"decision": "APPROVED", "justification": "Looks fine"})
    assert decided.status_code == 200
    body = decided.json()
    assert body["instance_status"] == "APPROVED"
    assert body["stages"][0]["status"] == "APPROVED"


@pytest.mark.asyncio
async def test_all_of_stage_requires_every_named_approver_and_any_rejection_ends_it_immediately(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (await client.post("/api/v1/workflows/definitions", json=_simple_definition_payload(ids, approval_mode="ALL_OF"))).json()
        await client.patch(f"/api/v1/workflows/definitions/{created['id']}", json={"status": "ACTIVE"})
        authenticate_as("AccessPilot.User", subject="requester-oid")
        submitted = (await client.post("/api/v1/workflows/requests", json={"workflow_definition_id": created["id"], "title": "Need something", "justification": "Because reasons"})).json()
        stage_id = submitted["stages"][0]["id"]

        authenticate_as("AccessPilot.User", subject="approver-a")
        after_first = (await client.post(f"/api/v1/workflows/requests/{submitted['id']}/stages/{stage_id}/decide", json={"decision": "APPROVED", "justification": "Fine by me"})).json()
        assert after_first["instance_status"] == "PENDING"
        assert after_first["stages"][0]["status"] == "PENDING"
        assert len(after_first["stages"][0]["decisions"]) == 1

        authenticate_as("AccessPilot.User", subject="approver-b")
        after_second = (await client.post(f"/api/v1/workflows/requests/{submitted['id']}/stages/{stage_id}/decide", json={"decision": "APPROVED", "justification": "Agreed"})).json()
    assert after_second["instance_status"] == "APPROVED"
    assert after_second["stages"][0]["status"] == "APPROVED"
    assert len(after_second["stages"][0]["decisions"]) == 2


@pytest.mark.asyncio
async def test_all_of_stage_any_single_rejection_ends_it_immediately(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (await client.post("/api/v1/workflows/definitions", json=_simple_definition_payload(ids, approval_mode="ALL_OF"))).json()
        await client.patch(f"/api/v1/workflows/definitions/{created['id']}", json={"status": "ACTIVE"})
        authenticate_as("AccessPilot.User", subject="requester-oid")
        submitted = (await client.post("/api/v1/workflows/requests", json={"workflow_definition_id": created["id"], "title": "Need something", "justification": "Because reasons"})).json()
        stage_id = submitted["stages"][0]["id"]

        authenticate_as("AccessPilot.User", subject="approver-a")
        after = (await client.post(f"/api/v1/workflows/requests/{submitted['id']}/stages/{stage_id}/decide", json={"decision": "REJECTED", "justification": "Not approved"})).json()
    assert after["instance_status"] == "REJECTED"
    assert after["stages"][0]["status"] == "REJECTED"

    # approver_b trying to vote after the stage is already finalized must be rejected
    authenticate_as("AccessPilot.User", subject="approver-b")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        blocked = await client.post(f"/api/v1/workflows/requests/{submitted['id']}/stages/{stage_id}/decide", json={"decision": "APPROVED", "justification": "Too late"})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "WORKFLOW_STAGE_ALREADY_DECIDED"


@pytest.mark.asyncio
async def test_condition_based_stage_skipping_and_missing_payload_field_never_errors(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    payload = {
        "name": "Conditional Workflow",
        "stages": [
            {"name": "Finance only", "approver_user_ids": [str(ids["approver_a"])], "condition_field": "department", "condition_operator": "EQUALS", "condition_value": "Finance"},
            {"name": "Final sign-off", "approver_user_ids": [str(ids["approver_b"])]},
        ],
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (await client.post("/api/v1/workflows/definitions", json=payload)).json()
        await client.patch(f"/api/v1/workflows/definitions/{created['id']}", json={"status": "ACTIVE"})

        # No payload at all -> condition_field 'department' is missing -> stage 1 is SKIPPED, not an error.
        authenticate_as("AccessPilot.User", subject="requester-oid")
        submitted = (await client.post("/api/v1/workflows/requests", json={"workflow_definition_id": created["id"], "title": "No dept given", "justification": "Because reasons"})).json()
    assert submitted["instance_status"] == "PENDING"
    assert submitted["stages"][0]["status"] == "SKIPPED"
    assert submitted["stages"][1]["status"] == "PENDING"
    assert submitted["stages"][1]["name"] == "Final sign-off"


@pytest.mark.asyncio
async def test_a_workflow_whose_every_stage_is_skipped_auto_approves_with_zero_decisions(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    payload = {
        "name": "All Conditional Workflow",
        "stages": [{"name": "Finance only", "approver_user_ids": [str(ids["approver_a"])], "condition_field": "department", "condition_operator": "EQUALS", "condition_value": "Finance"}],
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (await client.post("/api/v1/workflows/definitions", json=payload)).json()
        await client.patch(f"/api/v1/workflows/definitions/{created['id']}", json={"status": "ACTIVE"})
        authenticate_as("AccessPilot.User", subject="requester-oid")
        submitted = (await client.post("/api/v1/workflows/requests", json={"workflow_definition_id": created["id"], "title": "Not finance", "justification": "Because reasons"})).json()
    assert submitted["instance_status"] == "APPROVED"
    assert submitted["stages"][0]["status"] == "SKIPPED"
    assert all(len(s["decisions"]) == 0 for s in submitted["stages"])


@pytest.mark.asyncio
async def test_escalation_lets_a_fallback_finalize_any_of_and_all_of_outright(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (await client.post("/api/v1/workflows/definitions", json=_simple_definition_payload(ids, approval_mode="ALL_OF", with_fallback=True))).json()
        await client.patch(f"/api/v1/workflows/definitions/{created['id']}", json={"status": "ACTIVE"})
        authenticate_as("AccessPilot.User", subject="requester-oid")
        submitted = (await client.post("/api/v1/workflows/requests", json={"workflow_definition_id": created["id"], "title": "Need something", "justification": "Because reasons"})).json()
        stage_id = submitted["stages"][0]["id"]

        # Fallback cannot act before escalation.
        authenticate_as("AccessPilot.User", subject="fallback-x")
        too_early = await client.post(f"/api/v1/workflows/requests/{submitted['id']}/stages/{stage_id}/decide", json={"decision": "APPROVED", "justification": "Standing in"})
        assert too_early.status_code == 403
        assert too_early.json()["error"]["code"] == "FALLBACK_NOT_YET_AVAILABLE"

    async with db_override.factory() as session:
        stage = await session.get(WorkflowStageInstance, UUID(stage_id))
        stage.escalates_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()
        escalated = await sweep_workflow_escalations(session)
    assert escalated == 1

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        authenticate_as("AccessPilot.User", subject="fallback-x")
        after = (await client.post(f"/api/v1/workflows/requests/{submitted['id']}/stages/{stage_id}/decide", json={"decision": "APPROVED", "justification": "Standing in now"})).json()
    # ALL_OF stage, but the fallback's decision alone finalizes it outright — no second approver vote needed.
    assert after["instance_status"] == "APPROVED"
    assert after["stages"][0]["status"] == "APPROVED"
    assert len(after["stages"][0]["decisions"]) == 1


@pytest.mark.asyncio
async def test_escalation_sweep_is_idempotent(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (await client.post("/api/v1/workflows/definitions", json=_simple_definition_payload(ids, with_fallback=True))).json()
        await client.patch(f"/api/v1/workflows/definitions/{created['id']}", json={"status": "ACTIVE"})
        authenticate_as("AccessPilot.User", subject="requester-oid")
        submitted = (await client.post("/api/v1/workflows/requests", json={"workflow_definition_id": created["id"], "title": "Need something", "justification": "Because reasons"})).json()
        stage_id = submitted["stages"][0]["id"]

    async with db_override.factory() as session:
        stage = await session.get(WorkflowStageInstance, UUID(stage_id))
        stage.escalates_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()
        first_sweep = await sweep_workflow_escalations(session)
        second_sweep = await sweep_workflow_escalations(session)
    assert first_sweep == 1
    assert second_sweep == 0


@pytest.mark.asyncio
async def test_cancel_marks_current_stage_cancelled_and_only_requester_or_admin_can_cancel(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (await client.post("/api/v1/workflows/definitions", json=_simple_definition_payload(ids))).json()
        await client.patch(f"/api/v1/workflows/definitions/{created['id']}", json={"status": "ACTIVE"})
        authenticate_as("AccessPilot.User", subject="requester-oid")
        submitted = (await client.post("/api/v1/workflows/requests", json={"workflow_definition_id": created["id"], "title": "Need something", "justification": "Because reasons"})).json()

        authenticate_as("AccessPilot.User", subject="outsider-oid")
        denied = await client.post(f"/api/v1/workflows/requests/{submitted['id']}/cancel")
        assert denied.status_code == 403

        authenticate_as("AccessPilot.User", subject="requester-oid")
        cancelled = (await client.post(f"/api/v1/workflows/requests/{submitted['id']}/cancel")).json()
    assert cancelled["instance_status"] == "CANCELLED"
    assert cancelled["stages"][0]["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_a_completed_request_cannot_be_cancelled(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (await client.post("/api/v1/workflows/definitions", json=_simple_definition_payload(ids))).json()
        await client.patch(f"/api/v1/workflows/definitions/{created['id']}", json={"status": "ACTIVE"})
        authenticate_as("AccessPilot.User", subject="requester-oid")
        submitted = (await client.post("/api/v1/workflows/requests", json={"workflow_definition_id": created["id"], "title": "Need something", "justification": "Because reasons"})).json()
        stage_id = submitted["stages"][0]["id"]

        authenticate_as("AccessPilot.User", subject="approver-a")
        await client.post(f"/api/v1/workflows/requests/{submitted['id']}/stages/{stage_id}/decide", json={"decision": "APPROVED", "justification": "Fine"})

        authenticate_as("AccessPilot.User", subject="requester-oid")
        blocked = await client.post(f"/api/v1/workflows/requests/{submitted['id']}/cancel")
    assert blocked.status_code == 409


@pytest.mark.asyncio
async def test_admin_override_finalizes_a_stage_regardless_of_mode(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (await client.post("/api/v1/workflows/definitions", json=_simple_definition_payload(ids, approval_mode="ALL_OF"))).json()
        await client.patch(f"/api/v1/workflows/definitions/{created['id']}", json={"status": "ACTIVE"})
        authenticate_as("AccessPilot.User", subject="requester-oid")
        submitted = (await client.post("/api/v1/workflows/requests", json={"workflow_definition_id": created["id"], "title": "Need something", "justification": "Because reasons"})).json()
        stage_id = submitted["stages"][0]["id"]

        authenticate_as("AccessPilot.Admin")
        after = (await client.post(f"/api/v1/workflows/requests/{submitted['id']}/stages/{stage_id}/decide", json={"decision": "APPROVED", "justification": "Admin override"})).json()
    assert after["instance_status"] == "APPROVED"


@pytest.mark.asyncio
async def test_definition_with_history_cannot_be_deleted_but_can_be_disabled(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (await client.post("/api/v1/workflows/definitions", json=_simple_definition_payload(ids))).json()
        await client.patch(f"/api/v1/workflows/definitions/{created['id']}", json={"status": "ACTIVE"})
        authenticate_as("AccessPilot.User", subject="requester-oid")
        await client.post("/api/v1/workflows/requests", json={"workflow_definition_id": created["id"], "title": "Need something", "justification": "Because reasons"})

        authenticate_as("AccessPilot.Admin")
        blocked = await client.delete(f"/api/v1/workflows/definitions/{created['id']}")
        assert blocked.status_code == 409
        disabled = await client.patch(f"/api/v1/workflows/definitions/{created['id']}", json={"status": "DISABLED"})
    assert disabled.status_code == 200 and disabled.json()["status"] == "DISABLED"


@pytest.mark.asyncio
async def test_a_definition_with_no_history_can_be_deleted(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (await client.post("/api/v1/workflows/definitions", json=_simple_definition_payload(ids))).json()
        deleted = await client.delete(f"/api/v1/workflows/definitions/{created['id']}")
        listed = await client.get("/api/v1/workflows/definitions")
    assert deleted.status_code == 204
    assert listed.json() == []


@pytest.mark.asyncio
async def test_any_authenticated_user_can_list_active_definitions_only(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        draft = (await client.post("/api/v1/workflows/definitions", json=_simple_definition_payload(ids))).json()
        active_payload = _simple_definition_payload(ids)
        active_payload["name"] = "Active Workflow"
        active = (await client.post("/api/v1/workflows/definitions", json=active_payload)).json()
        await client.patch(f"/api/v1/workflows/definitions/{active['id']}", json={"status": "ACTIVE"})

        authenticate_as("AccessPilot.User", subject="outsider-oid")
        listed = (await client.get("/api/v1/workflows/definitions/active")).json()
    assert [d["id"] for d in listed] == [active["id"]]
    assert draft["id"] not in [d["id"] for d in listed]


@pytest.mark.asyncio
async def test_permission_gating_on_admin_endpoints(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.User", subject="outsider-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create = await client.post("/api/v1/workflows/definitions", json=_simple_definition_payload(ids))
        listing = await client.get("/api/v1/workflows/definitions")
        all_requests = await client.get("/api/v1/workflows/requests")
    assert create.status_code == 403
    assert listing.status_code == 403
    assert all_requests.status_code == 403


@pytest.mark.asyncio
async def test_mine_and_pending_my_decision_lists_are_scoped_correctly(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (await client.post("/api/v1/workflows/definitions", json=_simple_definition_payload(ids))).json()
        await client.patch(f"/api/v1/workflows/definitions/{created['id']}", json={"status": "ACTIVE"})
        authenticate_as("AccessPilot.User", subject="requester-oid")
        submitted = (await client.post("/api/v1/workflows/requests", json={"workflow_definition_id": created["id"], "title": "Need something", "justification": "Because reasons"})).json()

        mine = (await client.get("/api/v1/workflows/requests/mine")).json()
        assert len(mine) == 1 and mine[0]["id"] == submitted["id"]

        outsider_pending = (await client.get("/api/v1/workflows/requests/pending-my-decision")).json()
        assert outsider_pending == []  # requester is not the approver

        authenticate_as("AccessPilot.User", subject="approver-a")
        approver_pending = (await client.get("/api/v1/workflows/requests/pending-my-decision")).json()
    assert len(approver_pending) == 1 and approver_pending[0]["id"] == submitted["id"]
    assert approver_pending[0]["can_decide"] is True
