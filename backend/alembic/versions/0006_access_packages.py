"""Add access packages (bundles of Group/Role/Application+Role items) and a correlation table
linking a package assignment batch back to the individual access_assignments rows it created.

Revision ID: 0006_access_packages
Revises: 0005_application_assignments
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_access_packages"
down_revision = "0005_application_assignments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    table_names = inspector.get_table_names()

    if "access_packages" not in table_names:
        op.create_table(
            "access_packages",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("name", sa.String(length=255), nullable=False, unique=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=50), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )

    if "access_package_items" not in table_names:
        op.create_table(
            "access_package_items",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("package_id", sa.Uuid(), sa.ForeignKey("access_packages.id"), nullable=False),
            sa.Column("resource_type", sa.String(length=50), nullable=False),
            sa.Column("resource_id", sa.Uuid(), nullable=False),
            sa.Column("app_role_external_id", sa.String(length=100), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_access_package_items_package", "access_package_items", ["package_id"])

    if "access_package_assignments" not in table_names:
        op.create_table(
            "access_package_assignments",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("package_id", sa.Uuid(), sa.ForeignKey("access_packages.id"), nullable=False),
            sa.Column("package_assignment_id", sa.Uuid(), nullable=False),
            sa.Column("assignment_id", sa.Uuid(), sa.ForeignKey("access_assignments.id"), nullable=False, unique=True),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_access_package_assignments_batch", "access_package_assignments", ["package_assignment_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    table_names = inspector.get_table_names()

    if "access_package_assignments" in table_names:
        op.drop_index("ix_access_package_assignments_batch", table_name="access_package_assignments")
        op.drop_table("access_package_assignments")

    if "access_package_items" in table_names:
        op.drop_index("ix_access_package_items_package", table_name="access_package_items")
        op.drop_table("access_package_items")

    if "access_packages" in table_names:
        op.drop_table("access_packages")
