"""Add optional recurring sync interval to identity_providers.

Revision ID: 0004_provider_sync_schedule
Revises: 0003_provider_graph_credentials
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_provider_sync_schedule"
down_revision = "0003_provider_graph_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("identity_providers")}
    if "sync_interval_minutes" not in existing:
        op.add_column("identity_providers", sa.Column("sync_interval_minutes", sa.Integer(), nullable=True))


def downgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("identity_providers")}
    if "sync_interval_minutes" in existing:
        op.drop_column("identity_providers", "sync_interval_minutes")
