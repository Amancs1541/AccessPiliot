"""Add real_accounts_provisioned_count / birthright_assignments_created_count to onboarding_imports — Phase 9:
committing an import now provisions a REAL account (Microsoft Graph, or MockProvider in dev/tests) for each
new/changed identity and grants real Group/Role/Application membership per birthright policy immediately.

Revision ID: 0015_real_provisioning
Revises: 0014_birthright_policy
"""
from alembic import op
import sqlalchemy as sa

revision = "0015_real_provisioning"
down_revision = "0014_birthright_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("onboarding_imports")}
    if "real_accounts_provisioned_count" not in existing:
        op.add_column("onboarding_imports", sa.Column("real_accounts_provisioned_count", sa.Integer(), nullable=False, server_default="0"))
    if "birthright_assignments_created_count" not in existing:
        op.add_column("onboarding_imports", sa.Column("birthright_assignments_created_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("onboarding_imports")}
    if "birthright_assignments_created_count" in existing:
        op.drop_column("onboarding_imports", "birthright_assignments_created_count")
    if "real_accounts_provisioned_count" in existing:
        op.drop_column("onboarding_imports", "real_accounts_provisioned_count")
