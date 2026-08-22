"""Add non-secret provider configuration metadata.

Revision ID: 0002_provider_configuration_metadata
Revises: 0001_initial_schema
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_provider_meta"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("identity_providers")}
    columns = [("client_id", sa.String(length=255)), ("authority", sa.String(length=500)), ("api_audience", sa.String(length=500)), ("api_scope", sa.String(length=500)), ("redirect_uri_metadata", sa.JSON())]
    for name, column_type in columns:
        if name not in existing:
            op.add_column("identity_providers", sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("identity_providers")}
    for name in ("redirect_uri_metadata", "api_scope", "api_audience", "authority", "client_id"):
        if name in existing:
            op.drop_column("identity_providers", name)
