"""Add the Separation-of-Duties (SoD) engine: sod_policies, sod_policy_entities, sod_admins.

Revision ID: 0024_sod_engine
Revises: 0023_security_settings_logout
"""
from alembic import op
import sqlalchemy as sa

revision = "0024_sod_engine"
down_revision = "0023_security_settings_logout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "sod_policies" not in existing_tables:
        op.create_table(
            "sod_policies",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("name", sa.String(255), nullable=False, unique=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("severity", sa.String(20), nullable=False, server_default="MEDIUM"),
            sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    if "sod_policy_entities" not in existing_tables:
        op.create_table(
            "sod_policy_entities",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("sod_policy_id", sa.Uuid(), sa.ForeignKey("sod_policies.id"), nullable=False),
            sa.Column("conflict_side", sa.String(1), nullable=False),
            sa.Column("entity_type", sa.String(20), nullable=False),
            sa.Column("entity_id", sa.Uuid(), nullable=False),
            sa.Column("app_role_external_id", sa.String(100), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("sod_policy_id", "conflict_side", "entity_type", "entity_id", "app_role_external_id", name="uq_sod_policy_entity"),
        )
        op.create_index("ix_sod_policy_entities_policy", "sod_policy_entities", ["sod_policy_id"])
    if "sod_admins" not in existing_tables:
        op.create_table(
            "sod_admins",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False, unique=True),
            sa.Column("granted_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )


def downgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "sod_admins" in existing_tables:
        op.drop_table("sod_admins")
    if "sod_policy_entities" in existing_tables:
        op.drop_table("sod_policy_entities")
    if "sod_policies" in existing_tables:
        op.drop_table("sod_policies")
