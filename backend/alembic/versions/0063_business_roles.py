"""Business Roles (Step 1 of the Role Management / Entitlement Mapping plan): business_roles, business_role_items
(the IdP-role-to-business-role mapping itself, with an it_role_label reference field), business_role_owners, plus
resource_code/naming_convention reference columns on groups/roles/applications (same cosmetic, sync-safe pattern
as BirthrightPolicy.external_policy_id).

Revision ID: 0063_business_roles
Revises: 0062_reenable_scope
"""
from alembic import op
import sqlalchemy as sa

revision = "0063_business_roles"
down_revision = "0062_reenable_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_tables = inspector.get_table_names()

    for table in ("groups", "roles", "applications"):
        columns = {c["name"] for c in inspector.get_columns(table)}
        if "resource_code" not in columns:
            op.add_column(table, sa.Column("resource_code", sa.String(100), nullable=True))
            op.create_unique_constraint(f"uq_{table}_resource_code", table, ["resource_code"])
        if "naming_convention" not in columns:
            op.add_column(table, sa.Column("naming_convention", sa.String(255), nullable=True))

    if "business_roles" not in existing_tables:
        op.create_table(
            "business_roles",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("name", sa.String(255), nullable=False, unique=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("role_type", sa.String(30), nullable=False, server_default="BUSINESS"),
            sa.Column("department", sa.String(200), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
            sa.Column("risk_level", sa.String(20), nullable=False, server_default="LOW"),
            sa.Column("is_privileged", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("default_approver_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("default_fallback_approver_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("fallback_unlock_hours", sa.Integer(), nullable=True),
            sa.Column("review_frequency_days", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )

    if "business_role_items" not in existing_tables:
        op.create_table(
            "business_role_items",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("role_id", sa.Uuid(), sa.ForeignKey("business_roles.id"), nullable=False),
            sa.Column("resource_type", sa.String(50), nullable=False),
            sa.Column("resource_id", sa.Uuid(), nullable=False),
            sa.Column("app_role_external_id", sa.String(100), nullable=True),
            sa.Column("it_role_label", sa.String(255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_business_role_items_role", "business_role_items", ["role_id"])
        # One raw entitlement lives on at most one Business Role — the many-to-one mapping decided in the plan.
        op.create_unique_constraint(
            "uq_business_role_items_resource", "business_role_items",
            ["resource_type", "resource_id", "app_role_external_id"],
        )

    if "business_role_owners" not in existing_tables:
        op.create_table(
            "business_role_owners",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("role_id", sa.Uuid(), sa.ForeignKey("business_roles.id"), nullable=False),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("assigned_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_unique_constraint("uq_business_role_owners_role_user", "business_role_owners", ["role_id", "user_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in ("business_role_owners", "business_role_items", "business_roles"):
        if table in inspector.get_table_names():
            op.drop_table(table)
    for table in ("groups", "roles", "applications"):
        columns = {c["name"] for c in inspector.get_columns(table)}
        if "naming_convention" in columns:
            op.drop_column(table, "naming_convention")
        if "resource_code" in columns:
            op.drop_constraint(f"uq_{table}_resource_code", table, type_="unique")
            op.drop_column(table, "resource_code")
