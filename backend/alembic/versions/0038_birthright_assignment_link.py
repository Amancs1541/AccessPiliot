"""Add birthright_policy_id to access_assignments — marks which assignments were created BY birthright-policy
evaluation (vs. manually/admin-granted), so a later mover/attribute-change reconciliation pass can safely
auto-revoke only the ones a policy actually granted, never a manual grant.

Revision ID: 0038_birthright_assignment_link
Revises: 0037_nhi_credentials_list
"""
from alembic import op
import sqlalchemy as sa

revision = "0038_birthright_assignment_link"
down_revision = "0037_nhi_credentials_list"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("access_assignments")}
    if "birthright_policy_id" not in columns:
        op.add_column("access_assignments", sa.Column("birthright_policy_id", sa.Uuid(), sa.ForeignKey("birthright_policies.id"), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("access_assignments")}
    if "birthright_policy_id" in columns:
        op.drop_column("access_assignments", "birthright_policy_id")
