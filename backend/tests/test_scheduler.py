from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models import IdentityProvider
from app.workers.scheduler import run_due_syncs


async def _seeded_factory(**provider_kwargs):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        provider = IdentityProvider(**provider_kwargs)
        session.add(provider)
        await session.commit()
        provider_id = provider.id
    return engine, factory, provider_id


@pytest.mark.asyncio
async def test_sync_not_triggered_when_no_schedule_set(monkeypatch):
    engine, factory, _ = await _seeded_factory(name="Entra", type="ENTRA", status="CONFIGURED", tenant_id="t", sync_interval_minutes=None)
    calls = []
    monkeypatch.setattr("app.workers.scheduler.run_sync", lambda session, provider, request_id: calls.append(provider.id))

    attempted = await run_due_syncs(factory)
    assert attempted == 0
    assert calls == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_sync_triggered_when_never_synced_and_scheduled(monkeypatch):
    engine, factory, provider_id = await _seeded_factory(name="Entra", type="ENTRA", status="CONFIGURED", tenant_id="t", sync_interval_minutes=15, last_sync_at=None)
    calls = []
    async def fake_run_sync(session, provider, request_id):
        calls.append(provider.id)
    monkeypatch.setattr("app.workers.scheduler.run_sync", fake_run_sync)

    attempted = await run_due_syncs(factory)
    assert attempted == 1
    assert calls == [provider_id]
    await engine.dispose()


@pytest.mark.asyncio
async def test_sync_not_triggered_before_interval_elapses(monkeypatch):
    engine, factory, _ = await _seeded_factory(name="Entra", type="ENTRA", status="CONFIGURED", tenant_id="t", sync_interval_minutes=30, last_sync_at=datetime.now(timezone.utc) - timedelta(minutes=5))
    calls = []
    async def fake_run_sync(session, provider, request_id):
        calls.append(provider.id)
    monkeypatch.setattr("app.workers.scheduler.run_sync", fake_run_sync)

    attempted = await run_due_syncs(factory)
    assert attempted == 0
    assert calls == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_sync_triggered_after_interval_elapses(monkeypatch):
    engine, factory, provider_id = await _seeded_factory(name="Entra", type="ENTRA", status="CONFIGURED", tenant_id="t", sync_interval_minutes=30, last_sync_at=datetime.now(timezone.utc) - timedelta(minutes=45))
    calls = []
    async def fake_run_sync(session, provider, request_id):
        calls.append(provider.id)
    monkeypatch.setattr("app.workers.scheduler.run_sync", fake_run_sync)

    attempted = await run_due_syncs(factory)
    assert attempted == 1
    assert calls == [provider_id]
    await engine.dispose()


@pytest.mark.asyncio
async def test_one_provider_failure_does_not_block_others(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        failing = IdentityProvider(name="Failing", type="ENTRA", status="CONFIGURED", tenant_id="t1", sync_interval_minutes=15, last_sync_at=None)
        healthy = IdentityProvider(name="Healthy", type="ENTRA", status="CONFIGURED", tenant_id="t2", sync_interval_minutes=15, last_sync_at=None)
        session.add_all([failing, healthy])
        await session.commit()
        healthy_id = healthy.id

    calls = []
    async def fake_run_sync(session, provider, request_id):
        if provider.name == "Failing":
            raise RuntimeError("boom")
        calls.append(provider.id)
    monkeypatch.setattr("app.workers.scheduler.run_sync", fake_run_sync)

    attempted = await run_due_syncs(factory)
    assert attempted == 2
    assert calls == [healthy_id]
    await engine.dispose()
