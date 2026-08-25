"""Add fallback_approver_id to access_assignments and default_fallback_approver_id to access_packages —
lets a package configure a backup approver, so either the primary or the fallback approver may approve
a request (whichever acts first).

Revision ID: 0009_fallback_approver
Revises: 0008_self_activation
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_fallback_approver"
down_revision = "0008_self_activation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    assignment_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("access_assignments")}
    if "fallback_approver_id" not in assignment_columns:
        op.add_column("access_assignments", sa.Column("fallback_approver_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True))

    package_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("access_packages")}
    if "default_fallback_approver_id" not in package_columns:
        op.add_column("access_packages", sa.Column("default_fallback_approver_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True))


def downgrade() -> None:
    assignment_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("access_assignments")}
    if "fallback_approver_id" in assignment_columns:
        op.drop_column("access_assignments", "fallback_approver_id")

    package_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("access_packages")}
    if "default_fallback_approver_id" in package_columns:
        op.drop_column("access_packages", "default_fallback_approver_id")
