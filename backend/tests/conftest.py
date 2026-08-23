import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test.db")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("PROVIDER_MODE", "mock")

import pytest_asyncio


@pytest_asyncio.fixture(autouse=True, scope="session")
async def _prepare_shared_schema():
    from app.db.base import Base
    from app.db.session import engine
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield
