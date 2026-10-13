import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models import BirthrightPolicy, Group, IdentityAccount, IdentityProvider, LifecycleSettings, User
from app.services.provisioning import build_username_local_part, list_provisioning_domains, primary_identity_provider, provision_real_account, provision_secondary_accounts, sweep_missing_secondary_accounts


class TestSession:
    def __init__(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db():
    database = TestSession()
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with database.factory() as session:
        yield session
    await database.engine.dispose()


@pytest.mark.asyncio
async def test_no_configured_provider_returns_none_and_never_raises(db: AsyncSession):
    result = await provision_real_account(db, display_name="Alex Morgan", email="alex@company.com", department="IT", job_title="Support", request_id="test-1")
    assert result is None


@pytest.mark.asyncio
async def test_primary_identity_provider_prefers_entra_over_mock(db: AsyncSession):
    db.add(IdentityProvider(name="Mock", type="MOCK", status="CONNECTED", tenant_id="t"))
    db.add(IdentityProvider(name="Real", type="ENTRA", status="CONFIGURED", tenant_id="t2"))
    db.add(IdentityProvider(name="CSV / HR Onboarding", type="CSV", status="CONNECTED", tenant_id="local-csv"))
    await db.commit()
    provider = await primary_identity_provider(db)
    assert provider.type == "ENTRA"


@pytest.mark.asyncio
async def test_provisions_a_real_account_with_its_own_external_id(db: AsyncSession):
    provider = IdentityProvider(name="Mock", type="MOCK", status="CONNECTED", tenant_id="t")
    db.add(provider)
    await db.commit()

    row = await provision_real_account(db, display_name="Alex Morgan", email="alex.morgan@company.com", department="IT", job_title="IT Support Specialist", request_id="test-2")
    assert row is not None
    assert row.email == "alex.morgan@company.com"
    assert row.provider_id == provider.id
    assert row.external_id != "alex.morgan@company.com"  # a real connector-assigned object id, not the input email


@pytest.mark.asyncio
async def test_provisioning_the_same_email_twice_is_idempotent_not_duplicated(db: AsyncSession):
    provider = IdentityProvider(name="Mock", type="MOCK", status="CONNECTED", tenant_id="t")
    db.add(provider)
    await db.commit()

    first = await provision_real_account(db, display_name="Alex Morgan", email="alex.morgan@company.com", department="IT", job_title="IT Support Specialist", request_id="test-3a")
    second = await provision_real_account(db, display_name="Alex Morgan", email="alex.morgan@company.com", department="IT", job_title="IT Support Specialist", request_id="test-3b")
    assert first is not None and second is not None
    assert first.id == second.id

    rows = (await db.execute(select(User).where(User.email == "alex.morgan@company.com"))).scalars().all()
    assert len(rows) == 1


# --- Phase 11: mapping engine (verified domain + naming convention) ---

def test_username_local_part_applies_the_configured_convention():
    assert build_username_local_part("{first}.{last}", "Alex", "Morgan", "fallback@x.com") == "alex.morgan"


def test_username_local_part_supports_initials():
    assert build_username_local_part("{f}{last}", "Alex", "Morgan", "fallback@x.com") == "amorgan"


def test_username_local_part_strips_non_alphanumeric_characters():
    assert build_username_local_part("{first}.{last}", "O'Brien-Jane", "Smith Jr.", "fallback@x.com") == "obrienjane.smithjr"


def test_username_local_part_falls_back_when_no_convention_configured():
    assert build_username_local_part(None, "Alex", "Morgan", "alex.morgan@company.com") == "alex.morgan"


def test_username_local_part_falls_back_when_names_are_missing():
    assert build_username_local_part("{first}.{last}", None, None, "fallback.name@company.com") == "fallback.name"


@pytest.mark.asyncio
async def test_list_provisioning_domains_returns_the_connectors_domains(db: AsyncSession):
    db.add(IdentityProvider(name="Mock", type="MOCK", status="CONNECTED", tenant_id="t"))
    await db.commit()
    domains = await list_provisioning_domains(db)
    assert any(d.name == "northstar.io" and d.is_verified for d in domains)


@pytest.mark.asyncio
async def test_list_provisioning_domains_is_empty_with_no_provider_configured(db: AsyncSession):
    assert await list_provisioning_domains(db) == []


@pytest.mark.asyncio
async def test_provisioning_uses_the_configured_domain_instead_of_the_csv_email_domain(db: AsyncSession):
    """The core Phase 11 fix: an Admin-chosen, KNOWN VERIFIED domain overrides an arbitrary CSV email domain that
    the real IdP might otherwise reject."""
    provider = IdentityProvider(name="Mock", type="MOCK", status="CONNECTED", tenant_id="t", provisioning_domain="northstar.io", username_convention="{first}.{last}")
    db.add(provider)
    await db.commit()

    row = await provision_real_account(db, display_name="Alex Morgan", email="alex.morgan@some-unverified-domain.example", given_name="Alex", surname="Morgan", department="IT", job_title="Support", request_id="test-domain-1")
    assert row is not None
    assert row.email == "alex.morgan@northstar.io"  # NOT the CSV's own (unverified) domain


@pytest.mark.asyncio
async def test_provisioning_with_a_domain_but_no_convention_keeps_the_csv_local_part(db: AsyncSession):
    provider = IdentityProvider(name="Mock", type="MOCK", status="CONNECTED", tenant_id="t", provisioning_domain="northstar.io")
    db.add(provider)
    await db.commit()

    row = await provision_real_account(db, display_name="Alex Morgan", email="custom.nickname@some-other-domain.example", given_name="Alex", surname="Morgan", department="IT", job_title="Support", request_id="test-domain-2")
    assert row is not None
    assert row.email == "custom.nickname@northstar.io"


@pytest.mark.asyncio
async def test_provisioning_with_no_domain_configured_is_unchanged_from_before_this_feature(db: AsyncSession):
    provider = IdentityProvider(name="Mock", type="MOCK", status="CONNECTED", tenant_id="t")
    db.add(provider)
    await db.commit()

    row = await provision_real_account(db, display_name="Alex Morgan", email="alex.morgan@company.com", given_name="Alex", surname="Morgan", department="IT", job_title="Support", request_id="test-domain-3")
    assert row is not None
    assert row.email == "alex.morgan@company.com"


# --- account mapping: HR/CSV-onboarding counterpart to Joiner's auto-detection ---

async def _seed_two_providers_with_a_department_birthright(db: AsyncSession):
    """Primary (Entra, preferred by primary_identity_provider) + a second directory whose group a 'Finance'
    birthright policy grants — the exact shape required_providers_for_profile needs to detect that second
    directory is required, same setup test_joiner.py's own _seed() uses for Joiner's identical auto-detection."""
    primary = IdentityProvider(name="Primary", type="MOCK", status="CONNECTED", tenant_id="t1")
    secondary = IdentityProvider(name="Secondary AD", type="MOCK", status="CONNECTED", tenant_id="t2")
    db.add_all([primary, secondary])
    await db.flush()
    group = Group(provider_id=secondary.id, external_id="g-fin", name="Finance Group", status="ACTIVE", is_privileged=False)
    db.add(group)
    await db.flush()
    db.add(BirthrightPolicy(name="Finance", match_field="department", match_value="Finance", resource_type="GROUP", resource_id=group.id))
    await db.commit()
    return primary, secondary


@pytest.mark.asyncio
async def test_provision_secondary_accounts_creates_an_account_in_a_needed_second_directory(db: AsyncSession):
    primary, secondary = await _seed_two_providers_with_a_department_birthright(db)
    person = await provision_real_account(db, display_name="Priya Shah", email="priya.shah@company.com", given_name="Priya", surname="Shah", department="Finance", job_title="Analyst", request_id="test-secondary-1")
    assert person is not None and person.provider_id == primary.id

    created = await provision_secondary_accounts(db, person, department="Finance", job_title="Analyst", request_id="test-secondary-1b")
    assert len(created) == 1 and created[0].provider_id == secondary.id

    rows = (await db.execute(select(IdentityAccount).where(IdentityAccount.user_id == person.id))).scalars().all()
    assert len(rows) == 1 and rows[0].provider_id == secondary.id and rows[0].provisioned_by == "ONBOARDING"


@pytest.mark.asyncio
async def test_provision_secondary_accounts_is_idempotent(db: AsyncSession):
    _, secondary = await _seed_two_providers_with_a_department_birthright(db)
    person = await provision_real_account(db, display_name="Priya Shah", email="priya.shah@company.com", given_name="Priya", surname="Shah", department="Finance", job_title="Analyst", request_id="test-secondary-2")
    await provision_secondary_accounts(db, person, department="Finance", job_title="Analyst", request_id="test-secondary-2b")
    second_call = await provision_secondary_accounts(db, person, department="Finance", job_title="Analyst", request_id="test-secondary-2c")
    assert second_call == []
    rows = (await db.execute(select(IdentityAccount).where(IdentityAccount.user_id == person.id, IdentityAccount.provider_id == secondary.id))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_provision_secondary_accounts_skips_a_directory_switched_off_for_provisioning(db: AsyncSession):
    _, secondary = await _seed_two_providers_with_a_department_birthright(db)
    secondary.provision_joiners = False
    await db.commit()
    person = await provision_real_account(db, display_name="Priya Shah", email="priya.shah@company.com", given_name="Priya", surname="Shah", department="Finance", job_title="Analyst", request_id="test-secondary-3")
    created = await provision_secondary_accounts(db, person, department="Finance", job_title="Analyst", request_id="test-secondary-3b")
    assert created == []


@pytest.mark.asyncio
async def test_provision_secondary_accounts_returns_nothing_when_no_other_directory_is_needed(db: AsyncSession):
    await _seed_two_providers_with_a_department_birthright(db)
    person = await provision_real_account(db, display_name="Owen Brady", email="owen.brady@company.com", given_name="Owen", surname="Brady", department="Sales", job_title="Account Executive", request_id="test-secondary-4")
    created = await provision_secondary_accounts(db, person, department="Sales", job_title="Account Executive", request_id="test-secondary-4b")
    assert created == []


# --- the dedicated retry sweep: catches a missing secondary account even with no new sync for that person ---

@pytest.mark.asyncio
async def test_sweep_missing_secondary_accounts_does_nothing_when_no_interval_is_configured(db: AsyncSession):
    """NULL/unset (the default) means this sweep is off — a missing secondary account only ever gets retried the
    next time that person happens to be synced, exactly as before this feature existed."""
    _, secondary = await _seed_two_providers_with_a_department_birthright(db)
    person = await provision_real_account(db, display_name="Priya Shah", email="priya.shah@company.com", given_name="Priya", surname="Shah", department="Finance", job_title="Analyst", request_id="test-sweep-1")
    provisioned = await sweep_missing_secondary_accounts(db)
    assert provisioned == 0
    rows = (await db.execute(select(IdentityAccount).where(IdentityAccount.user_id == person.id, IdentityAccount.provider_id == secondary.id))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_sweep_fills_in_a_missing_secondary_account_once_configured(db: AsyncSession):
    """The actual scenario this exists for: a person was onboarded (primary account only, e.g. their needed
    second directory was unreachable or not yet connected) and nothing ever syncs them again — the sweep catches
    it anyway, with no new sync required."""
    _, secondary = await _seed_two_providers_with_a_department_birthright(db)
    db.add(LifecycleSettings(secondary_account_retry_interval_minutes=15))
    await db.commit()
    person = await provision_real_account(db, display_name="Priya Shah", email="priya.shah@company.com", given_name="Priya", surname="Shah", department="Finance", job_title="Analyst", request_id="test-sweep-2")

    provisioned = await sweep_missing_secondary_accounts(db)
    assert provisioned == 1
    rows = (await db.execute(select(IdentityAccount).where(IdentityAccount.user_id == person.id, IdentityAccount.provider_id == secondary.id))).scalars().all()
    assert len(rows) == 1 and rows[0].provisioned_by == "ONBOARDING"


@pytest.mark.asyncio
async def test_sweep_respects_the_configured_interval_and_does_not_rerun_early(db: AsyncSession):
    from datetime import datetime, timedelta, timezone

    _, secondary = await _seed_two_providers_with_a_department_birthright(db)
    settings = LifecycleSettings(secondary_account_retry_interval_minutes=15, last_secondary_account_retry_sweep_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    db.add(settings)
    await db.commit()
    person = await provision_real_account(db, display_name="Priya Shah", email="priya.shah@company.com", given_name="Priya", surname="Shah", department="Finance", job_title="Analyst", request_id="test-sweep-3")

    provisioned = await sweep_missing_secondary_accounts(db)
    assert provisioned == 0
    rows = (await db.execute(select(IdentityAccount).where(IdentityAccount.user_id == person.id, IdentityAccount.provider_id == secondary.id))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_sweep_actually_commits_its_work_not_just_flushes():
    """Regression test for a real bug caught live: provision_secondary_accounts() only flushes (correct — its
    caller, commit_import, commits everything together at the end), but the sweep IS the top-level caller for a
    person who is never re-synced, so it must commit itself — a plain flush is invisible to any OTHER session
    once this one closes. Deliberately uses a SEPARATE session to verify, since reading back through the same
    session the sweep ran in would pass even without a commit (flushed rows are visible within one transaction)
    and would have silently missed this exact bug."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with factory() as session:
        _, secondary = await _seed_two_providers_with_a_department_birthright(session)
        session.add(LifecycleSettings(secondary_account_retry_interval_minutes=15))
        await session.commit()
        person = await provision_real_account(session, display_name="Priya Shah", email="priya.shah@company.com", given_name="Priya", surname="Shah", department="Finance", job_title="Analyst", request_id="test-sweep-commit")
        person_id, secondary_id = person.id, secondary.id
        provisioned = await sweep_missing_secondary_accounts(session)
        assert provisioned == 1

    async with factory() as fresh_session:  # a brand-new session/connection — only ever sees COMMITTED data
        rows = (await fresh_session.execute(select(IdentityAccount).where(IdentityAccount.user_id == person_id, IdentityAccount.provider_id == secondary_id))).scalars().all()
    assert len(rows) == 1

    await engine.dispose()
