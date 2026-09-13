"""Add soc_dashboard_layouts — per-viewer widget layout (visible/order) for the new Security Operations
dashboard, gated behind the new AccessPilot.SoCAdmin Entra role. The first genuinely per-viewer preference table
in this app; every other settings table (SecuritySettings, BrandingSettings, SodNotificationSettings) is a
shared singleton.

Revision ID: 0033_soc_dashboard
Revises: 0032_sod_exc_req_assign_link
"""
from alembic import op
import sqlalchemy as sa

revision = "0033_soc_dashboard"
down_revision = "0032_sod_exc_req_assign_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "soc_dashboard_layouts" not in existing_tables:
        op.create_table(
            "soc_dashboard_layouts",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, unique=True),
            sa.Column("widgets", sa.JSON(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )


def downgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "soc_dashboard_layouts" in existing_tables:
        op.drop_table("soc_dashboard_layouts")
