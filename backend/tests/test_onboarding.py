import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AccessAssignment, AuditLog, Group, IdentityProvider, User
from app.security.auth import AuthenticatedUser, require_authenticated_user

VALID_CSV = (
    "employeeId,firstName,lastName,email,department,jobTitle,status\n"
    "EMP1001,John,Smith,john.smith@company.com,Finance,Financial Analyst,ACTIVE\n"
    "EMP1002,Jane,Doe,jane.doe@company.com,Engineering,Software Engineer,ACTIVE\n"
)


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


@pytest.mark.asyncio
async def test_upload_creates_a_validated_import_with_two_new_identities(db_override):
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/onboarding/csv", json={"filename": "employees.csv", "content": VALID_CSV})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "VALIDATED"
    assert body["total_records"] == 2
    assert body["created_count"] == 2
    assert body["updated_count"] == 0
    assert body["failed_count"] == 0

    async with db_override.factory() as session:
        from sqlalchemy import select
        users = (await session.execute(select(User))).scalars().all()
    assert users == []  # validation must NOT touch `users` — only commit does


@pytest.mark.asyncio
async def test_preview_lists_planned_action_per_row(db_override):
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        uploaded = await client.post("/api/v1/onboarding/csv", json={"filename": "employees.csv", "content": VALID_CSV})
        import_id = uploaded.json()["id"]
        preview = await client.get(f"/api/v1/onboarding/imports/{import_id}/preview")
    assert preview.status_code == 200
    rows = preview.json()
    assert [row["employee_id"] for row in rows] == ["EMP1001", "EMP1002"]
    assert all(row["action"] == "CREATE" for row in rows)


@pytest.mark.asyncio
async def test_commit_creates_identities_that_are_immediately_visible_to_the_rest_of_the_app(db_override):
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        uploaded = await client.post("/api/v1/onboarding/csv", json={"filename": "employees.csv", "content": VALID_CSV})
        import_id = uploaded.json()["id"]
        committed = await client.post(f"/api/v1/onboarding/imports/{import_id}/commit")
    assert committed.status_code == 200
    assert committed.json()["status"] == "COMMITTED"

    async with db_override.factory() as session:
        from sqlalchemy import select
        users = (await session.execute(select(User).order_by(User.external_id))).scalars().all()
        provider = (await session.execute(select(IdentityProvider).where(IdentityProvider.type == "CSV"))).scalar_one()
    assert [user.external_id for user in users] == ["EMP1001", "EMP1002"]
    assert all(user.provider_id == provider.id for user in users)
    assert users[0].display_name == "John Smith"
    assert users[0].status == "ACTIVE"


@pytest.mark.asyncio
async def test_a_second_upload_detects_updates_and_no_change_against_committed_identities(db_override):
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/onboarding/csv", json={"filename": "employees.csv", "content": VALID_CSV})
        await client.post(f"/api/v1/onboarding/imports/{first.json()['id']}/commit")

        changed_csv = (
            "employeeId,firstName,lastName,email,department,jobTitle,status\n"
            "EMP1001,John,Smith,john.smith@company.com,Marketing,Financial Analyst,ACTIVE\n"  # department changed -> UPDATE
            "EMP1002,Jane,Doe,jane.doe@company.com,Engineering,Software Engineer,ACTIVE\n"  # unchanged -> NO_CHANGE
        )
        second = await client.post("/api/v1/onboarding/csv", json={"filename": "employees2.csv", "content": changed_csv})
    body = second.json()
    assert body["updated_count"] == 1
    assert body["no_change_count"] == 1


@pytest.mark.asyncio
async def test_terminated_employee_disables_the_existing_identity_on_commit(db_override):
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/onboarding/csv", json={"filename": "employees.csv", "content": VALID_CSV})
        await client.post(f"/api/v1/onboarding/imports/{first.json()['id']}/commit")

        leaver_csv = (
            "employeeId,firstName,lastName,email,department,jobTitle,status\n"
            "EMP1001,John,Smith,john.smith@company.com,Finance,Financial Analyst,TERMINATED\n"
        )
        second = await client.post("/api/v1/onboarding/csv", json={"filename": "leavers.csv", "content": leaver_csv})
        second_id = second.json()["id"]
        assert second.json()["disabled_count"] == 1
        await client.post(f"/api/v1/onboarding/imports/{second_id}/commit")

    async with db_override.factory() as session:
        from sqlalchemy import select
        user = (await session.execute(select(User).where(User.external_id == "EMP1001"))).scalar_one()
    assert user.status == "DISABLED"


@pytest.mark.asyncio
async def test_missing_required_column_fails_validation_without_creating_records(db_override):
    authenticate_as("AccessPilot.Admin")
    bad_csv = "employeeId,firstName,lastName,email,status\nEMP1,John,Smith,j@x.com,ACTIVE\n"  # no `department`
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/onboarding/csv", json={"filename": "bad.csv", "content": bad_csv})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "VALIDATION_FAILED"
    assert "department" in body["error_summary"]["missingColumns"]


@pytest.mark.asyncio
async def test_row_level_errors_are_isolated_and_do_not_block_other_rows(db_override):
    authenticate_as("AccessPilot.Admin")
    mixed_csv = (
        "employeeId,firstName,lastName,email,department,status\n"
        "EMP1,John,Smith,not-an-email,Finance,ACTIVE\n"  # bad email -> ERROR
        "EMP2,Jane,Doe,jane@x.com,Engineering,ACTIVE\n"  # fine -> CREATE
        "EMP2,Jane,Doe,jane@x.com,Engineering,ACTIVE\n"  # duplicate employeeId -> ERROR
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/onboarding/csv", json={"filename": "mixed.csv", "content": mixed_csv})
    body = response.json()
    assert body["status"] == "VALIDATED"
    assert body["failed_count"] == 2
    assert body["created_count"] == 1


@pytest.mark.asyncio
async def test_cannot_commit_an_import_twice(db_override):
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        uploaded = await client.post("/api/v1/onboarding/csv", json={"filename": "employees.csv", "content": VALID_CSV})
        import_id = uploaded.json()["id"]
        await client.post(f"/api/v1/onboarding/imports/{import_id}/commit")
        second_commit = await client.post(f"/api/v1/onboarding/imports/{import_id}/commit")
    assert second_commit.status_code == 409


@pytest.mark.asyncio
async def test_a_normal_user_cannot_upload_or_manage_onboarding(db_override):
    authenticate_as("AccessPilot.User", subject="regular-user-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/onboarding/csv", json={"filename": "employees.csv", "content": VALID_CSV})
    assert response.status_code == 403


async def _seed_a_group(factory):
    async with factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        group = Group(provider_id=provider.id, external_id="g1", name="Security Team", status="ACTIVE", is_privileged=False)
        session.add(group)
        await session.commit()
        return group.id


@pytest.mark.asyncio
async def test_terminating_a_leaver_revokes_every_non_final_assignment_on_commit(db_override):
    """Phase 7: a TERMINATED row must not just disable the identity — it must also revoke whatever real/eligible
    access that identity still holds, reusing revoke_assignment() unmodified."""
    group_id = await _seed_a_group(db_override.factory)
    authenticate_as("AccessPilot.Admin")

    joiner_csv = "employeeId,firstName,lastName,email,department,jobTitle,status\nEMP2001,Leaving,Soon,leaving.soon@company.com,Finance,Analyst,ACTIVE\n"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        joined = await client.post("/api/v1/onboarding/csv", json={"filename": "joiner.csv", "content": joiner_csv})
        await client.post(f"/api/v1/onboarding/imports/{joined.json()['id']}/commit")

        # A MOCK provider exists in this test (_seed_a_group), so this joiner gets a real-provisioned identity —
        # look up by employee_id (provider-independent), not external_id (now a connector-assigned id, not "EMP2001").
        async with db_override.factory() as session:
            user = (await session.execute(select(User).where(User.employee_id == "EMP2001"))).scalar_one()

        # One assignment goes all the way to ACTIVE (real provider grant + real provider revoke on leave);
        # a second is left ELIGIBLE (never activated, no provider call needed either way).
        active_created = await client.post("/api/v1/assignments", json={"user_id": str(user.id), "resource_type": "GROUP", "resource_id": str(group_id), "assignment_type": "PERMANENT", "justification": "Need it."})
        await client.post(f"/api/v1/assignments/{active_created.json()['id']}/activate", json={"duration_hours": 2, "justification": "Activating."})
        eligible_created = await client.post("/api/v1/assignments", json={"user_id": str(user.id), "resource_type": "GROUP", "resource_id": str(group_id), "assignment_type": "PERMANENT", "justification": "Also need this."})

        leaver_csv = "employeeId,firstName,lastName,email,department,jobTitle,status\nEMP2001,Leaving,Soon,leaving.soon@company.com,Finance,Analyst,TERMINATED\n"
        leaver_uploaded = await client.post("/api/v1/onboarding/csv", json={"filename": "leaver.csv", "content": leaver_csv})
        assert leaver_uploaded.json()["disabled_count"] == 1
        leaver_committed = await client.post(f"/api/v1/onboarding/imports/{leaver_uploaded.json()['id']}/commit")

    body = leaver_committed.json()
    assert body["status"] == "COMMITTED"
    assert body["access_revoked_count"] == 2
    assert body["access_revoke_failed_count"] == 0

    async with db_override.factory() as session:
        user = (await session.execute(select(User).where(User.employee_id == "EMP2001"))).scalar_one()
        assert user.status == "DISABLED"
        assignments = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == user.id))).scalars().all()
        assert len(assignments) == 2
        assert all(a.status == "REVOKED" and a.revoked_at is not None for a in assignments)
        justifications = [entry.metadata_json.get("justification") for entry in (await session.execute(select(AuditLog).where(AuditLog.action == "ASSIGNMENT_REVOKED"))).scalars().all()]
        assert all("Automated leaver revocation" in (j or "") for j in justifications)


@pytest.mark.asyncio
async def test_updating_an_active_employee_never_touches_their_assignments(db_override):
    """A mover (attribute change while still ACTIVE) must not trigger any revocation — only TERMINATED does."""
    group_id = await _seed_a_group(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    joiner_csv = "employeeId,firstName,lastName,email,department,jobTitle,status\nEMP2002,Still,Here,still.here@company.com,Finance,Analyst,ACTIVE\n"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        joined = await client.post("/api/v1/onboarding/csv", json={"filename": "joiner.csv", "content": joiner_csv})
        await client.post(f"/api/v1/onboarding/imports/{joined.json()['id']}/commit")
        async with db_override.factory() as session:
            user = (await session.execute(select(User).where(User.employee_id == "EMP2002"))).scalar_one()
        await client.post("/api/v1/assignments", json={"user_id": str(user.id), "resource_type": "GROUP", "resource_id": str(group_id), "assignment_type": "PERMANENT", "justification": "Need it."})

        mover_csv = "employeeId,firstName,lastName,email,department,jobTitle,status\nEMP2002,Still,Here,still.here@company.com,Marketing,Analyst,ACTIVE\n"
        mover_uploaded = await client.post("/api/v1/onboarding/csv", json={"filename": "mover.csv", "content": mover_csv})
        assert mover_uploaded.json()["updated_count"] == 1
        mover_committed = await client.post(f"/api/v1/onboarding/imports/{mover_uploaded.json()['id']}/commit")

    assert mover_committed.json()["access_revoked_count"] == 0
    async with db_override.factory() as session:
        assignment = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == user.id))).scalar_one()
        assert assignment.status == "ELIGIBLE"


@pytest.mark.asyncio
async def test_a_joiner_gets_exactly_one_identity_row_when_a_real_provider_is_configured(db_override):
    """Phase 10: the core 'no redundant users' guarantee. A CSV joiner never produces two rows (a CSV bookkeeping
    row plus a separate real-account row) — when a real connector is available, there is exactly ONE row, landed
    directly under the real provider, carrying employee_id + source for traceability."""
    await _seed_a_group(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    csv_content = "employeeId,firstName,lastName,email,department,status\nEMP4001,Real,Account,real.account@company.com,IT,ACTIVE\n"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        uploaded = await client.post("/api/v1/onboarding/csv", json={"filename": "joiner.csv", "content": csv_content})
        await client.post(f"/api/v1/onboarding/imports/{uploaded.json()['id']}/commit")

    async with db_override.factory() as session:
        rows = (await session.execute(select(User).where(User.employee_id == "EMP4001"))).scalars().all()
        assert len(rows) == 1
        row = rows[0]
        provider = await session.get(IdentityProvider, row.provider_id)
    assert provider.type == "MOCK"  # landed directly on the real connector, not the CSV bookkeeping provider
    assert row.source == "CSV_ONBOARDING"
    assert row.external_id != "EMP4001"  # a real connector-assigned id, not the CSV key


@pytest.mark.asyncio
async def test_a_joiner_falls_back_to_one_csv_bookkeeping_row_when_no_real_provider_is_configured(db_override):
    authenticate_as("AccessPilot.Admin")
    csv_content = "employeeId,firstName,lastName,email,department,status\nEMP4002,Local,Only,local.only@company.com,IT,ACTIVE\n"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        uploaded = await client.post("/api/v1/onboarding/csv", json={"filename": "joiner.csv", "content": csv_content})
        await client.post(f"/api/v1/onboarding/imports/{uploaded.json()['id']}/commit")

    async with db_override.factory() as session:
        rows = (await session.execute(select(User).where(User.employee_id == "EMP4002"))).scalars().all()
        assert len(rows) == 1
        row = rows[0]
        provider = await session.get(IdentityProvider, row.provider_id)
    assert provider.type == "CSV"
    assert row.source == "CSV_ONBOARDING"
    assert row.external_id == "EMP4002"


@pytest.mark.asyncio
async def test_an_identity_graduates_in_place_when_a_real_provider_becomes_available_later(db_override):
    """The self-healing case: a joiner lands on the CSV fallback (no provider configured yet, or their domain
    wasn't verified), picks up an ELIGIBLE assignment there, then a real connector becomes available — the NEXT
    commit for the same employeeId must reuse the SAME identity row (same id, same assignment history), not spin
    up a second one."""
    authenticate_as("AccessPilot.Admin")
    csv_content = "employeeId,firstName,lastName,email,department,status\nEMP4003,Graduate,Later,graduate.later@company.com,IT,ACTIVE\n"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/onboarding/csv", json={"filename": "joiner.csv", "content": csv_content})
        first_commit = await client.post(f"/api/v1/onboarding/imports/{first.json()['id']}/commit")
    assert first_commit.json()["real_accounts_provisioned_count"] == 0

    async with db_override.factory() as session:
        before = (await session.execute(select(User).where(User.employee_id == "EMP4003"))).scalar_one()
        before_provider = await session.get(IdentityProvider, before.provider_id)
        assert before_provider.type == "CSV"
        original_id = before.id
        # This ELIGIBLE assignment (created while still CSV-only) must survive graduation. Inserted directly via
        # the ORM with a placeholder resource_id — no real Group needs to exist for this test, since the point is
        # only to prove the assignment row itself stays attached to the same identity through graduation.
        from uuid import uuid4
        assignment = AccessAssignment(provider_id=before.provider_id, user_id=before.id, resource_type="GROUP", resource_id=uuid4(), assignment_type="PERMANENT", status="ELIGIBLE", justification="pre-graduation")
        session.add(assignment)
        await session.commit()
        assignment_id = assignment.id

    # A real connector now becomes available.
    async with db_override.factory() as session:
        mock_provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t2")
        session.add(mock_provider)
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        second = await client.post("/api/v1/onboarding/csv", json={"filename": "joiner2.csv", "content": csv_content})
        assert second.json()["no_change_count"] == 1
        second_commit = await client.post(f"/api/v1/onboarding/imports/{second.json()['id']}/commit")
    assert second_commit.json()["real_accounts_provisioned_count"] == 1

    async with db_override.factory() as session:
        rows = (await session.execute(select(User).where(User.employee_id == "EMP4003"))).scalars().all()
        assert len(rows) == 1  # still exactly one row — graduated in place, not duplicated
        after = rows[0]
        after_provider = await session.get(IdentityProvider, after.provider_id)
        assert after.id == original_id  # same identity, so all history stays attached
        assert after_provider.type == "MOCK"
        surviving_assignment = await session.get(AccessAssignment, assignment_id)
        assert surviving_assignment is not None
        assert surviving_assignment.user_id == original_id
