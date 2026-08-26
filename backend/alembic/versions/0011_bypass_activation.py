"""Add bypass_activation to access_assignments — lets an Admin, at direct-assignment creation time, grant real
access immediately (no eligible/activate step) and lock the assignment so the end user cannot self-deactivate it
(an Admin still can).

Revision ID: 0011_bypass_activation
Revises: 0010_package_approver_flow
"""
from alembic import op
import sqlalchemy as sa

revision = "0011_bypass_activation"
down_revision = "0010_package_approver_flow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("access_assignments")}
    if "bypass_activation" not in existing:
        op.add_column("access_assignments", sa.Column("bypass_activation", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("access_assignments")}
    if "bypass_activation" in existing:
        op.drop_column("access_assignments", "bypass_activation")
