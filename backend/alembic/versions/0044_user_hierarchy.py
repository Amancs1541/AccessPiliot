"""User org-hierarchy tagging: employee_category (EMPLOYEE/MANAGER, nullable/unclassified by default) and
manager_id (self-referential FK) on users. Purely additive, no backfill. See
app.models.models.User / app.services.user_hierarchy.

Revision ID: 0044_user_hierarchy
Revises: 0043_departments
"""
from alembic import op
import sqlalchemy as sa

revision = "0044_user_hierarchy"
down_revision = "0043_departments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "employee_category" not in columns:
        op.add_column("users", sa.Column("employee_category", sa.String(20), nullable=True))
    if "manager_id" not in columns:
        op.add_column("users", sa.Column("manager_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "manager_id" in columns:
        op.drop_column("users", "manager_id")
    if "employee_category" in columns:
        op.drop_column("users", "employee_category")
