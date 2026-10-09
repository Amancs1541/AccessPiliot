"""Group Label classification + Department-Manager mapping: new group_labels lookup table (admin-defined custom
labels), groups.group_label column (STANDARD/PRIVILEGED built-in or a custom name — set only at creation, never
touched by directory sync), and departments.manager_id (one manager per department, used to auto-suggest a
Joiner's manager). Entirely additive.

Revision ID: 0069_group_label_dept_mgr
Revises: 0068_review_and_package_wf
"""
from alembic import op
import sqlalchemy as sa

revision = "0069_group_label_dept_mgr"
down_revision = "0068_review_and_package_wf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_tables = inspector.get_table_names()

    if "group_labels" not in existing_tables:
        op.create_table(
            "group_labels",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("name", sa.String(100), nullable=False, unique=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )

    group_columns = {c["name"] for c in inspector.get_columns("groups")}
    if "group_label" not in group_columns:
        op.add_column("groups", sa.Column("group_label", sa.String(100), nullable=True))

    department_columns = {c["name"] for c in inspector.get_columns("departments")}
    if "manager_id" not in department_columns:
        op.add_column("departments", sa.Column("manager_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    department_columns = {c["name"] for c in inspector.get_columns("departments")}
    if "manager_id" in department_columns:
        op.drop_column("departments", "manager_id")

    group_columns = {c["name"] for c in inspector.get_columns("groups")}
    if "group_label" in group_columns:
        op.drop_column("groups", "group_label")

    if "group_labels" in inspector.get_table_names():
        op.drop_table("group_labels")
