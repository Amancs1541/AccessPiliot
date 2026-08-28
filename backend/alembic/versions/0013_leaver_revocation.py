"""Add access_revoked_count / access_revoke_failed_count to onboarding_imports — Phase 7: when a CSV row marks an
employee TERMINATED, committing the import now also revokes every one of that identity's non-final
AccessAssignment rows (reusing the existing revoke_assignment()), and these two counters report the outcome.

Revision ID: 0013_leaver_revocation
Revises: 0012_onboarding_imports
"""
from alembic import op
import sqlalchemy as sa

revision = "0013_leaver_revocation"
down_revision = "0012_onboarding_imports"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("onboarding_imports")}
    if "access_revoked_count" not in existing:
        op.add_column("onboarding_imports", sa.Column("access_revoked_count", sa.Integer(), nullable=False, server_default="0"))
    if "access_revoke_failed_count" not in existing:
        op.add_column("onboarding_imports", sa.Column("access_revoke_failed_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("onboarding_imports")}
    if "access_revoke_failed_count" in existing:
        op.drop_column("onboarding_imports", "access_revoke_failed_count")
    if "access_revoked_count" in existing:
        op.drop_column("onboarding_imports", "access_revoked_count")
