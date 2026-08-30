import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models import IdentityProvider, User
from app.services.provisioning import build_username_local_part, list_provisioning_domains, primary_identity_provider, provision_real_account


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
