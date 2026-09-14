"""Add nhi_credentials (the full secret/certificate list, not just the soonest expiry) to applications, for the
NHI detail page's "Certificates & Secrets" section.

Revision ID: 0037_nhi_credentials_list
Revises: 0036_nhi_type_taxonomy
"""
from alembic import op
import sqlalchemy as sa

revision = "0037_nhi_credentials_list"
down_revision = "0036_nhi_type_taxonomy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("applications")}
    if "nhi_credentials" not in columns:
        op.add_column("applications", sa.Column("nhi_credentials", sa.JSON(), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("applications")}
    if "nhi_credentials" in columns:
        op.drop_column("applications", "nhi_credentials")
