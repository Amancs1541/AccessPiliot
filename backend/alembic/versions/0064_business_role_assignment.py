"""Business Roles Step 3: wire a Business Role into the real AccessAssignment lifecycle — business_role_id (same
provenance-tag pattern as birthright_policy_id/group_role_mapping_id) and role_assignment_id (a plain batch id,
same idea as AccessPackageAssignment.package_assignment_id, grouping one role-grant's items together) added
directly onto access_assignments, no new table.

Revision ID: 0064_business_role_assign
Revises: 0063_business_roles
"""
from alembic import op
import sqlalchemy as sa

revision = "0064_business_role_assign"
down_revision = "0063_business_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("access_assignments")}
    if "business_role_id" not in columns:
        op.add_column("access_assignments", sa.Column("business_role_id", sa.Uuid(), sa.ForeignKey("business_roles.id"), nullable=True))
    if "role_assignment_id" not in columns:
        op.add_column("access_assignments", sa.Column("role_assignment_id", sa.Uuid(), nullable=True))
        op.create_index("ix_access_assignments_role_assignment", "access_assignments", ["role_assignment_id"])


def downgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("access_assignments")}
    if "role_assignment_id" in columns:
        op.drop_column("access_assignments", "role_assignment_id")
    if "business_role_id" in columns:
        op.drop_column("access_assignments", "business_role_id")
