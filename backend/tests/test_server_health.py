import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from datetime import datetime, timezone

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AuditLog, IdentityProvider, SyncError, SyncRun, User
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


def authenticate_as(role: str) -> None:
    async def dependency():
        return AuthenticatedUser("server-health-oid", "Test User", "user@example.com", "tenant", (role,), {})
    app.dependency_overrides[require_authenticated_user] = dependency


@pytest_asyncio.fixture(autouse=True)
async def _reset_server_health_state():
    """server_health_state's buffers are process-global (by design — in production they must accumulate real
    requests across the whole process lifetime, unlike DB state, which each test file already isolates via its
    own in-memory engine). Left uncleared, unrelated earlier test files' deliberately-triggered error responses
    (a 403, a 422, a real 5xx from an error-handling test) bleed into this file's API-card/incident assertions,
    depending purely on pytest's run order — a real test-isolation gap, not a production one."""
    from app.services import server_health_state
    server_health_state.REQUEST_LOG.clear()
    server_health_state.QUERY_DURATIONS.clear()
    server_health_state.WORKER_TICK_HISTORY.clear()
    server_health_state.WORKER_LAST_TICK.clear()
    yield


@pytest.mark.asyncio
async def test_a_plain_user_is_denied_server_health(db_override):
    authenticate_as("AccessPilot.User")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/server-health")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_a_plain_admin_is_denied_server_health(db_override):
    """Exclusive to AccessPilot.ServerAdmin — the same treatment SOC and Separation of Duties both already have;
    a plain Admin, despite having broad access elsewhere, sees nothing here."""
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/server-health")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_a_real_serveradmin_sees_real_live_data(db_override):
    """V2: every field is computed from a real source — no worker Tasks exist in this test process (the app's
    lifespan, which creates them, is never triggered by ASGITransport), so the shape asserted here is what a
    real ServerAdmin sees on a process where nothing is running yet, not a fixed mock count."""
    async with db_override.factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        actor = User(provider_id=provider.id, external_id="actor-oid", email="actor@x.com", display_name="Actor User", status="ACTIVE")
        session.add(actor)
        await session.flush()
        # A real user's own action (has an actor) — must NOT show up in the live event log, which is scoped to
        # the server's own activity, not ordinary business audit trail.
        session.add(AuditLog(actor_user_id=actor.id, action="ASSIGNMENT_CREATED", target_type="ASSIGNMENT", request_id="r1", result="SUCCESS"))
        # A real system-generated entry (no actor at all, exactly like every worker's own record_audit call) —
        # this one SHOULD show up.
        session.add(AuditLog(actor_user_id=None, action="SYNC_COMPLETED", target_type="PROVIDER", target_id=provider.id, provider_id=provider.id, request_id="scheduled-sync-1", result="SUCCESS"))
        await session.commit()

    authenticate_as("AccessPilot.ServerAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/server-health")
    assert response.status_code == 200
    body = response.json()
    assert body["is_mock"] is False
    assert {card["name"] for card in body["services"]} == {"API", "Database", "Graph connector", "Background workers", "Auth / Portal", "Identity providers"}
    assert {worker["name"] for worker in body["workers"]} == {"Entra sync worker", "Access expiry sweep", "Scheduled activation worker", "SoD exception expiry worker"}
    # No workers actually run in a test process (lifespan never fires here) — this must say so honestly, not
    # pretend workers are healthy.
    workers_card = next(card for card in body["services"] if card["name"] == "Background workers")
    assert workers_card["variant"] == "mock"
    assert {w["name"] for w in body["workflows"]} == {"Entra sync worker", "Access expiry sweep", "Scheduled activation worker", "SoD exception expiry worker", "Onboarding CSV import", "Access Package assignment"}
    sync_workflow = next(w for w in body["workflows"] if w["name"] == "Entra sync worker")
    assert sync_workflow["kind"] == "background"
    assert sync_workflow["variant"] == "mock"  # honestly reflects no worker Task in this test process
    assert sync_workflow["recent_activity"] == "1 in the last 24h"  # the real SYNC_COMPLETED entry seeded above
    onboarding_workflow = next(w for w in body["workflows"] if w["name"] == "Onboarding CSV import")
    assert onboarding_workflow["kind"] == "on-demand"
    assert onboarding_workflow["status"] == "NEVER RUN"  # no OnboardingImport row seeded in this test
    providers_card = next(card for card in body["services"] if card["name"] == "Identity providers")
    assert providers_card["metric"] == "1"
    assert body["database"]["audit_log_rows"] >= 1
    assert body["database"]["replication_lag"] == "n/a — single node"
    assert body["database"]["last_backup"] == "Not configured"
    assert any("Sync Completed" in event["message"] for event in body["events"])
    assert not any("Assignment Created" in event["message"] for event in body["events"])


@pytest.mark.asyncio
async def test_a_plain_user_is_denied_troubleshooting(db_override):
    authenticate_as("AccessPilot.User")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/server-health/troubleshooting")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_troubleshooting_reports_no_incident_when_everything_is_healthy(db_override):
    """Real, not a fixed "there's always something wrong" demo — when every service card is healthy, the
    dependency chain still degrades the sync-worker/SoD nodes honestly (no worker Task exists in a test process,
    ASGITransport never triggers the app's real lifespan), but there must be no fabricated incident on top of
    that, and no root causes/checklist items invented for a problem that doesn't exist."""
    authenticate_as("AccessPilot.ServerAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/server-health/troubleshooting")
    assert response.status_code == 200
    body = response.json()
    assert body["incident"] is None
    assert body["root_causes"] == []
    assert body["checklist"] == []
    assert len(body["error_trend"]) == 12
    assert all(point["count"] == 0 for point in body["error_trend"])


@pytest.mark.asyncio
async def test_troubleshooting_diagnoses_a_real_graph_throttling_incident(db_override):
    async with db_override.factory() as session:
        provider = IdentityProvider(name="Directory", type="ENTRA", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        run = SyncRun(provider_id=provider.id, status="FAILED", started_at=datetime.now(timezone.utc))
        session.add(run)
        await session.flush()
        for _ in range(3):
            session.add(SyncError(sync_run_id=run.id, resource_type="ROLE", external_id="role-1", error_code="GRAPH_THROTTLED", error_message="429 Too Many Requests"))
        await session.commit()

    authenticate_as("AccessPilot.ServerAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/server-health/troubleshooting")
    assert response.status_code == 200
    body = response.json()
    assert body["incident"] is not None
    assert "Graph connector" in body["incident"]["title"]
    assert body["root_causes"][0]["title"] == "Graph API throttling (429s)"
    assert "3" in body["root_causes"][0]["detail"]
    assert any(item["label"] == "Confirm which calls are failing" and item["done"] for item in body["checklist"])
    assert sum(point["count"] for point in body["error_trend"]) == 3
    graph_node = next(node for node in body["dependency_chain"] if node["name"] == "Graph connector")
    assert graph_node["variant"] == "warn"
    sod_node = next(node for node in body["dependency_chain"] if node["name"] == "SoD detective scan")
    assert sod_node["status"] == "stale data"
