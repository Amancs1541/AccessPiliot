"""onboarding_imports.birthright_assignments_revoked_count — how many birthright grants a CSV import took away
from movers (people whose department/job title changed), the revoke half of the mover reconcile. Additive.

Revision ID: 0050_import_bright_revoked
Revises: 0049_review_sched_group_owners
"""
from alembic import op
import sqlalchemy as sa

revision = "0050_import_bright_revoked"
down_revision = "0049_review_sched_group_owners"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("onboarding_imports")}
    if "birthright_assignments_revoked_count" not in existing:
        op.add_column("onboarding_imports", sa.Column("birthright_assignments_revoked_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("onboarding_imports")}
    if "birthright_assignments_revoked_count" in existing:
        op.drop_column("onboarding_imports", "birthright_assignments_revoked_count")
