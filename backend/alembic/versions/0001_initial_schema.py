"""Create the AccessPilot foundation schema.

Revision ID: 0001_initial_schema
Revises:
"""
import asyncio
from typing import Sequence, Union

from alembic import op
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.base import Base
from app.models import models  # noqa: F401

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    asyncio.run(_create_schema())


def downgrade() -> None:
    asyncio.run(_drop_schema())


async def _create_schema() -> None:
    from app.core.config import get_settings
    engine = create_async_engine(get_settings().database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def _drop_schema() -> None:
    from app.core.config import get_settings
    engine = create_async_engine(get_settings().database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()
