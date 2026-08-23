"""Add encrypted-at-rest Graph connector credential columns to identity_providers.

Revision ID: 0003_provider_graph_credentials
Revises: 0002_provider_meta
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_provider_graph_credentials"
down_revision = "0002_provider_meta"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("identity_providers")}
    columns = [("graph_client_id", sa.String(length=255)), ("graph_client_secret_encrypted", sa.Text())]
    for name, column_type in columns:
        if name not in existing:
            op.add_column("identity_providers", sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("identity_providers")}
    for name in ("graph_client_secret_encrypted", "graph_client_id"):
        if name in existing:
            op.drop_column("identity_providers", name)
