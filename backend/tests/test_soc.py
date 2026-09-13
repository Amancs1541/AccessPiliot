from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AccessAssignment, AuditLog, Group, IdentityProvider, SocDashboardLayout, SodException, SodPolicy, SodPolicyEntity, User
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


def authenticate_as(role: str, subject: str = "soc-oid") -> None:
    async def dependency():
        return AuthenticatedUser(subject, "Test User", "user@example.com", "tenant", (role,), {})
    app.dependency_overrides[require_authenticated_user] = dependency


async def _seed_directory(factory):
    async with factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        soc_user = User(provider_id=provider.id, external_id="soc-oid", email="soc@x.com", display_name="SoC Admin", status="ACTIVE")
        target_user = User(provider_id=provider.id, external_id="target-user", email="target@x.com", display_name="Target User", status="ACTIVE")
        session.add_all([soc_user, target_user])
        await session.commit()
        return {"provider_id": provider.id, "soc_user_id": soc_user.id, "target_user_id": target_user.id}


@pytest.mark.asyncio
async def test_a_plain_user_is_denied_every_soc_endpoint(db_override):
    authenticate_as("AccessPilot.User")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/v1/soc/fields")).status_code == 403
        assert (await client.get("/api/v1/soc/layout")).status_code == 403
        assert (await client.post("/api/v1/soc/widget-data", json={"widgets": []})).status_code == 403


@pytest.mark.asyncio
async def test_fields_lists_every_source_and_builtin_widget(db_override):
    authenticate_as("AccessPilot.SoCAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/soc/fields")
    assert response.status_code == 200
    body = response.json()
    source_names = {s["source"] for s in body["sources"]}
    assert source_names == {"audit_logs", "assignments"}
    assignments_fields = next(s["fields"] for s in body["sources"] if s["source"] == "assignments")
    assert {"status", "resource_type", "assignment_type"} <= {f["field"] for f in assignments_fields}
    assert len(body["builtin_widgets"]) == 6


@pytest.mark.asyncio
async def test_a_never_customized_layout_returns_the_six_default_builtin_widgets(db_override):
    await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoCAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/soc/layout")
    assert response.status_code == 200
    widgets = response.json()["widgets"]
    assert len(widgets) == 6
    assert all(w["source"] == "builtin" and w["visible"] for w in widgets)


@pytest.mark.asyncio
async def test_a_layout_saved_under_the_old_pre_builder_shape_falls_back_to_the_default(db_override):
    """Real bug found live: the first SoCAdmin to use the dashboard (before the widget-builder rebuild) had saved
    a layout row shaped {id, visible, order} with no title/kind/source — the new schema requires those, and
    deserializing the old row 500'd on every single load afterward. Must fail open to the default instead of
    breaking the page over stale personal preference data."""
    ids = await _seed_directory(db_override.factory)
    async with db_override.factory() as session:
        session.add(SocDashboardLayout(user_id=ids["soc_user_id"], widgets=[{"id": "active_sessions", "visible": True, "order": 0}]))
        await session.commit()

    authenticate_as("AccessPilot.SoCAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/soc/layout")
    assert response.status_code == 200
    assert len(response.json()["widgets"]) == 6


@pytest.mark.asyncio
async def test_a_plain_admin_is_denied_the_soc_dashboard(db_override):
    """The user's explicit ask: this dashboard is exclusive to a real AccessPilot.SoCAdmin — unlike the SoD
    dashboard, a plain Admin does NOT get read-only oversight here."""
    await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/v1/soc/fields")).status_code == 403
        assert (await client.get("/api/v1/soc/layout")).status_code == 403
        assert (await client.post("/api/v1/soc/widget-data", json={"widgets": []})).status_code == 403


@pytest.mark.asyncio
async def test_saving_a_reordered_layout_with_a_custom_widget_round_trips(db_override):
    await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoCAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        custom_layout = {"widgets": [
            {"id": "active_sessions", "title": "Active sessions", "kind": "card", "source": "builtin", "builtin_id": "active_sessions", "visible": True, "order": 1},
            {"id": "my-custom-graph", "title": "Revokes by resource type", "kind": "bar", "source": "assignments", "group_by": "resource_type", "filters": [{"field": "status", "value": "REVOKED"}], "visible": True, "order": 0},
        ]}
        saved = await client.put("/api/v1/soc/layout", json=custom_layout)
        assert saved.status_code == 200
        by_id = {w["id"]: w for w in saved.json()["widgets"]}
        assert by_id["active_sessions"]["order"] == 1
        assert by_id["my-custom-graph"]["order"] == 0
        assert by_id["my-custom-graph"]["group_by"] == "resource_type"
        assert by_id["my-custom-graph"]["filters"] == [{"field": "status", "value": "REVOKED"}]

        refetched = await client.get("/api/v1/soc/layout")
    refetched_by_id = {w["id"]: w for w in refetched.json()["widgets"]}
    assert refetched_by_id["my-custom-graph"]["group_by"] == "resource_type"


@pytest.mark.asyncio
async def test_builtin_card_widget_computes_a_real_count(db_override):
    ids = await _seed_directory(db_override.factory)
    async with db_override.factory() as session:
        session.add(AccessAssignment(provider_id=ids["provider_id"], user_id=ids["target_user_id"], resource_type="GROUP", resource_id=ids["target_user_id"], assignment_type="PERMANENT", status="ACTIVE"))
        await session.commit()

    authenticate_as("AccessPilot.SoCAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/soc/widget-data", json={"widgets": [{"id": "w1", "title": "Active sessions", "kind": "card", "source": "builtin", "builtin_id": "active_sessions", "order": 0}]})
    assert response.status_code == 200
    assert response.json()["results"]["w1"]["value"] == 1


@pytest.mark.asyncio
async def test_custom_bar_widget_groups_and_filters_correctly(db_override):
    ids = await _seed_directory(db_override.factory)
    async with db_override.factory() as session:
        session.add(AccessAssignment(provider_id=ids["provider_id"], user_id=ids["target_user_id"], resource_type="GROUP", resource_id=ids["target_user_id"], assignment_type="PERMANENT", status="REVOKED"))
        session.add(AccessAssignment(provider_id=ids["provider_id"], user_id=ids["target_user_id"], resource_type="ROLE", resource_id=ids["target_user_id"], assignment_type="PERMANENT", status="REVOKED"))
        session.add(AccessAssignment(provider_id=ids["provider_id"], user_id=ids["target_user_id"], resource_type="GROUP", resource_id=ids["target_user_id"], assignment_type="PERMANENT", status="ACTIVE"))
        await session.commit()

    authenticate_as("AccessPilot.SoCAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        widget = {"id": "w1", "title": "Revokes by resource type", "kind": "bar", "source": "assignments", "group_by": "resource_type", "filters": [{"field": "status", "value": "REVOKED"}], "order": 0}
        response = await client.post("/api/v1/soc/widget-data", json={"widgets": [widget]})
    assert response.status_code == 200
    series = {row["label"]: row["value"] for row in response.json()["results"]["w1"]["series"]}
    assert series == {"GROUP": 1, "ROLE": 1}


@pytest.mark.asyncio
async def test_custom_timeseries_widget_buckets_by_day_and_respects_filters(db_override):
    ids = await _seed_directory(db_override.factory)
    async with db_override.factory() as session:
        session.add(AuditLog(actor_user_id=ids["soc_user_id"], action="BREAKGLASS_LOGIN", target_type="BREAKGLASS_ACCOUNT", request_id="r1", result="SUCCESS"))
        session.add(AuditLog(actor_user_id=ids["soc_user_id"], action="USER_SYNCED", target_type="USER", request_id="r2", result="SUCCESS"))
        await session.commit()

    authenticate_as("AccessPilot.SoCAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        widget = {"id": "w1", "title": "Break-glass logins over time", "kind": "timeseries", "source": "audit_logs", "filters": [{"field": "action", "value": "BREAKGLASS_LOGIN"}], "order": 0}
        response = await client.post("/api/v1/soc/widget-data", json={"widgets": [widget]})
    assert response.status_code == 200
    series = response.json()["results"]["w1"]["series"]
    assert len(series) == 30
    assert sum(point["count"] for point in series) == 1


@pytest.mark.asyncio
async def test_an_invalid_group_by_field_is_rejected(db_override):
    await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoCAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        widget = {"id": "w1", "title": "Bad", "kind": "bar", "source": "assignments", "group_by": "not_a_real_field", "order": 0}
        response = await client.post("/api/v1/soc/widget-data", json={"widgets": [widget]})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_clicking_a_point_on_the_builtin_activity_timeline_returns_that_days_real_events(db_override):
    """The user's ask: clicking a point on the "Platform activity" chart must show the actual events (and who
    was involved) behind that day's count, not just the number."""
    ids = await _seed_directory(db_override.factory)
    today = datetime.now(timezone.utc).date().isoformat()
    async with db_override.factory() as session:
        session.add(AuditLog(actor_user_id=ids["soc_user_id"], action="BREAKGLASS_LOGIN", target_type="BREAKGLASS_ACCOUNT", request_id="r1", result="SUCCESS"))
        session.add(AuditLog(actor_user_id=ids["soc_user_id"], action="USER_SYNCED", target_type="USER", request_id="r2", result="SUCCESS"))
        await session.commit()

    authenticate_as("AccessPilot.SoCAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        widget = {"id": "activity_timeline", "title": "Platform activity (30 days)", "kind": "timeseries", "source": "builtin", "builtin_id": "activity_timeline", "order": 0}
        response = await client.post("/api/v1/soc/widget-drilldown", json={"widget": widget, "date": today})
    assert response.status_code == 200
    rows = response.json()["rows"]
    assert len(rows) == 2
    assert {r["action"] for r in rows} == {"BREAKGLASS_LOGIN", "USER_SYNCED"}
    assert all(r["actor_display_name"] == "SoC Admin" for r in rows)


@pytest.mark.asyncio
async def test_drilldown_on_a_custom_timeseries_respects_its_filters(db_override):
    ids = await _seed_directory(db_override.factory)
    today = datetime.now(timezone.utc).date().isoformat()
    async with db_override.factory() as session:
        session.add(AuditLog(actor_user_id=ids["soc_user_id"], action="BREAKGLASS_LOGIN", target_type="BREAKGLASS_ACCOUNT", request_id="r1", result="SUCCESS"))
        session.add(AuditLog(actor_user_id=ids["soc_user_id"], action="USER_SYNCED", target_type="USER", request_id="r2", result="SUCCESS"))
        await session.commit()

    authenticate_as("AccessPilot.SoCAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        widget = {"id": "w1", "title": "Break-glass logins over time", "kind": "timeseries", "source": "audit_logs", "filters": [{"field": "action", "value": "BREAKGLASS_LOGIN"}], "order": 0}
        response = await client.post("/api/v1/soc/widget-drilldown", json={"widget": widget, "date": today})
    assert response.status_code == 200
    rows = response.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["action"] == "BREAKGLASS_LOGIN"


@pytest.mark.asyncio
async def test_drilldown_on_assignments_returns_the_target_users_name(db_override):
    ids = await _seed_directory(db_override.factory)
    today = datetime.now(timezone.utc).date().isoformat()
    async with db_override.factory() as session:
        session.add(AccessAssignment(provider_id=ids["provider_id"], user_id=ids["target_user_id"], resource_type="GROUP", resource_id=ids["target_user_id"], assignment_type="PERMANENT", status="ACTIVE"))
        await session.commit()

    authenticate_as("AccessPilot.SoCAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        widget = {"id": "w1", "title": "Assignments over time", "kind": "timeseries", "source": "assignments", "order": 0}
        response = await client.post("/api/v1/soc/widget-drilldown", json={"widget": widget, "date": today})
    assert response.status_code == 200
    rows = response.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["user_display_name"] == "Target User"


@pytest.mark.asyncio
async def test_clicking_open_sod_violations_lists_conflicts_including_ones_covered_by_an_exception(db_override):
    """The card counts every current conflict, exempted or not — matching the real SoD admin page's own
    convention (it never hides an exempted violation from its list either). Clicking through must show that
    same full picture, including the exception detail the plain count can't convey on its own."""
    ids = await _seed_directory(db_override.factory)
    async with db_override.factory() as session:
        group_a = Group(provider_id=ids["provider_id"], external_id="group-a", name="HR-Team", status="ACTIVE")
        group_b = Group(provider_id=ids["provider_id"], external_id="group-b", name="Students", status="ACTIVE")
        session.add_all([group_a, group_b])
        await session.flush()
        policy = SodPolicy(name="HR vs Students", severity="MEDIUM", status="ACTIVE")
        session.add(policy)
        await session.flush()
        session.add_all([
            SodPolicyEntity(sod_policy_id=policy.id, conflict_side="A", entity_type="GROUP", entity_id=group_a.id),
            SodPolicyEntity(sod_policy_id=policy.id, conflict_side="B", entity_type="GROUP", entity_id=group_b.id),
        ])
        session.add(AccessAssignment(provider_id=ids["provider_id"], user_id=ids["target_user_id"], resource_type="GROUP", resource_id=group_a.id, assignment_type="PERMANENT", status="ACTIVE"))
        session.add(AccessAssignment(provider_id=ids["provider_id"], user_id=ids["target_user_id"], resource_type="GROUP", resource_id=group_b.id, assignment_type="PERMANENT", status="ACTIVE"))
        session.add(SodException(sod_policy_id=policy.id, user_id=ids["target_user_id"], justification="Approved for testing", expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc)))
        await session.commit()

    authenticate_as("AccessPilot.SoCAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        card_data = await client.post("/api/v1/soc/widget-data", json={"widgets": [{"id": "open_sod_violations", "title": "SoD violations", "kind": "card", "source": "builtin", "builtin_id": "open_sod_violations", "order": 0}]})
        assert card_data.json()["results"]["open_sod_violations"]["value"] == 1  # still counted, even though exempted

        widget = {"id": "open_sod_violations", "title": "Open SoD violations", "kind": "card", "source": "builtin", "builtin_id": "open_sod_violations", "order": 0}
        response = await client.post("/api/v1/soc/widget-drilldown", json={"widget": widget})
    assert response.status_code == 200
    rows = response.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["user_display_name"] == "Target User"
    assert rows[0]["policy_name"] == "HR vs Students"
    assert rows[0]["exception_active"] is True
    assert set(rows[0]["side_a"]) == {"HR-Team"}
    assert set(rows[0]["side_b"]) == {"Students"}


@pytest.mark.asyncio
async def test_drilldown_is_rejected_for_a_panel_with_nothing_further_to_show(db_override):
    """high_signal_events is already a raw list of individual events — there's nothing more granular underneath
    it, unlike a card's total or a bar/list's grouped totals, both of which now drill into real rows."""
    await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoCAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        widget = {"id": "high_signal_events", "title": "High-signal events", "kind": "list", "source": "builtin", "builtin_id": "high_signal_events", "order": 0}
        response = await client.post("/api/v1/soc/widget-drilldown", json={"widget": widget})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_clicking_a_builtin_card_shows_the_real_rows_behind_its_count(db_override):
    """The user's ask: every card/graph should show the real list of events behind it when clicked, not just
    the timeseries chart and the SoD violations card that already did."""
    ids = await _seed_directory(db_override.factory)
    async with db_override.factory() as session:
        session.add(AccessAssignment(provider_id=ids["provider_id"], user_id=ids["target_user_id"], resource_type="GROUP", resource_id=ids["target_user_id"], assignment_type="PERMANENT", status="ACTIVE"))
        session.add(AccessAssignment(provider_id=ids["provider_id"], user_id=ids["target_user_id"], resource_type="ROLE", resource_id=ids["target_user_id"], assignment_type="PERMANENT", status="REVOKED"))
        await session.commit()

    authenticate_as("AccessPilot.SoCAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        widget = {"id": "active_sessions", "title": "Active sessions", "kind": "card", "source": "builtin", "builtin_id": "active_sessions", "order": 0}
        response = await client.post("/api/v1/soc/widget-drilldown", json={"widget": widget})
    assert response.status_code == 200
    rows = response.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["status"] == "ACTIVE"
    assert rows[0]["user_display_name"] == "Target User"


@pytest.mark.asyncio
async def test_clicking_a_custom_card_respects_its_filters(db_override):
    ids = await _seed_directory(db_override.factory)
    async with db_override.factory() as session:
        session.add(AccessAssignment(provider_id=ids["provider_id"], user_id=ids["target_user_id"], resource_type="GROUP", resource_id=ids["target_user_id"], assignment_type="PERMANENT", status="REVOKED"))
        session.add(AccessAssignment(provider_id=ids["provider_id"], user_id=ids["target_user_id"], resource_type="GROUP", resource_id=ids["target_user_id"], assignment_type="PERMANENT", status="ACTIVE"))
        await session.commit()

    authenticate_as("AccessPilot.SoCAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        widget = {"id": "w1", "title": "Revoked count", "kind": "card", "source": "assignments", "filters": [{"field": "status", "value": "REVOKED"}], "order": 0}
        response = await client.post("/api/v1/soc/widget-drilldown", json={"widget": widget})
    assert response.status_code == 200
    rows = response.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["status"] == "REVOKED"


@pytest.mark.asyncio
async def test_clicking_a_bar_entry_shows_the_rows_in_that_group(db_override):
    ids = await _seed_directory(db_override.factory)
    async with db_override.factory() as session:
        session.add(AccessAssignment(provider_id=ids["provider_id"], user_id=ids["target_user_id"], resource_type="GROUP", resource_id=ids["target_user_id"], assignment_type="PERMANENT", status="REVOKED"))
        session.add(AccessAssignment(provider_id=ids["provider_id"], user_id=ids["target_user_id"], resource_type="ROLE", resource_id=ids["target_user_id"], assignment_type="PERMANENT", status="REVOKED"))
        await session.commit()

    authenticate_as("AccessPilot.SoCAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        widget = {"id": "w1", "title": "Revokes by resource type", "kind": "bar", "source": "assignments", "group_by": "resource_type", "filters": [{"field": "status", "value": "REVOKED"}], "order": 0}
        response = await client.post("/api/v1/soc/widget-drilldown", json={"widget": widget, "group_value": "GROUP"})
    assert response.status_code == 200
    rows = response.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["resource_type"] == "GROUP"


@pytest.mark.asyncio
async def test_clicking_a_bar_or_list_widget_without_a_group_value_is_rejected(db_override):
    await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoCAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        widget = {"id": "w1", "title": "Revokes by resource type", "kind": "bar", "source": "assignments", "group_by": "resource_type", "order": 0}
        response = await client.post("/api/v1/soc/widget-drilldown", json={"widget": widget})
    assert response.status_code == 422
