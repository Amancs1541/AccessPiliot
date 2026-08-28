import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models import IdentityProvider, User
from app.services.provisioning import primary_identity_provider, provision_real_account


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
