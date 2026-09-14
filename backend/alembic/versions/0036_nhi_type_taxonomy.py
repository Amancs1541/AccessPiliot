"""Widen NHI classification beyond "service principal": adds nhi_type_overridden to applications so an
NHIAdmin can manually reclassify an identity (e.g. as an AI agent, bot, or API) without a later sync stomping it
back to whatever the connector auto-detects.

Revision ID: 0036_nhi_type_taxonomy
Revises: 0035_nhi_management
"""
from alembic import op
import sqlalchemy as sa

revision = "0036_nhi_type_taxonomy"
down_revision = "0035_nhi_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("applications")}
    if "nhi_type_overridden" not in columns:
        op.add_column("applications", sa.Column("nhi_type_overridden", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("applications")}
    if "nhi_type_overridden" in columns:
        op.drop_column("applications", "nhi_type_overridden")
