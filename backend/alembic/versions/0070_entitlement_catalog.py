"""Entitlement Catalog: new entitlement_catalog_entries table, one row per real Group/Role, or one specific
AppRole on an Application — a plain-English description, risk tier, and accountable owner, independent of
whether the entitlement is bundled into a Package or Business Role yet. Entirely additive.

Revision ID: 0070_entitlement_catalog
Revises: 0069_group_label_dept_mgr
"""
from alembic import op
import sqlalchemy as sa

revision = "0070_entitlement_catalog"
down_revision = "0069_group_label_dept_mgr"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_tables = inspector.get_table_names()

    if "entitlement_catalog_entries" not in existing_tables:
        op.create_table(
            "entitlement_catalog_entries",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("resource_type", sa.String(50), nullable=False),
            sa.Column("resource_id", sa.Uuid(), nullable=False),
            sa.Column("app_role_external_id", sa.String(100), nullable=False, server_default=""),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("risk_tier", sa.String(20), nullable=False, server_default="LOW"),
            sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("resource_type", "resource_id", "app_role_external_id", name="uq_entitlement_catalog_resource"),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "entitlement_catalog_entries" in inspector.get_table_names():
        op.drop_table("entitlement_catalog_entries")
