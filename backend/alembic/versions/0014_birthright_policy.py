"""Add birthright_policies — Phase 8: an attribute-driven rule (department/job_title -> Group/Role/Application)
that auto-creates an ELIGIBLE AccessAssignment for a matching identity, evaluated on CSV onboarding commit.

Revision ID: 0014_birthright_policy
Revises: 0013_leaver_revocation
"""
from alembic import op
import sqlalchemy as sa

revision = "0014_birthright_policy"
down_revision = "0013_leaver_revocation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "birthright_policies" not in existing_tables:
        op.create_table(
            "birthright_policies",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("name", sa.String(255), nullable=False, unique=True),
            sa.Column("match_field", sa.String(50), nullable=False),
            sa.Column("match_value", sa.String(255), nullable=False),
            sa.Column("resource_type", sa.String(50), nullable=False),
            sa.Column("resource_id", sa.Uuid(), nullable=False),
            sa.Column("app_role_external_id", sa.String(100), nullable=True),
            sa.Column("assignment_type", sa.String(50), nullable=False, server_default="PERMANENT"),
            sa.Column("status", sa.String(50), nullable=False, server_default="ACTIVE"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_birthright_policies_match", "birthright_policies", ["match_field", "match_value"])


def downgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "birthright_policies" in existing_tables:
        op.drop_table("birthright_policies")
