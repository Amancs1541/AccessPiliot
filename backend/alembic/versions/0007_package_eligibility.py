"""Add package eligibility (who may self-request a package: individual users or whole groups) and a
default approver used when an eligible end user requests it.

Revision ID: 0007_package_eligibility
Revises: 0006_access_packages
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_package_eligibility"
down_revision = "0006_access_packages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    table_names = inspector.get_table_names()

    existing_columns = {column["name"] for column in inspector.get_columns("access_packages")} if "access_packages" in table_names else set()
    if "default_approver_id" not in existing_columns:
        op.add_column("access_packages", sa.Column("default_approver_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True))

    if "access_package_eligibility" not in table_names:
        op.create_table(
            "access_package_eligibility",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("package_id", sa.Uuid(), sa.ForeignKey("access_packages.id"), nullable=False),
            sa.Column("principal_type", sa.String(length=20), nullable=False),
            sa.Column("principal_id", sa.Uuid(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("package_id", "principal_type", "principal_id", name="uq_package_eligibility_principal"),
        )
        op.create_index("ix_access_package_eligibility_package", "access_package_eligibility", ["package_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    table_names = inspector.get_table_names()

    if "access_package_eligibility" in table_names:
        op.drop_index("ix_access_package_eligibility_package", table_name="access_package_eligibility")
        op.drop_table("access_package_eligibility")

    existing_columns = {column["name"] for column in inspector.get_columns("access_packages")} if "access_packages" in table_names else set()
    if "default_approver_id" in existing_columns:
        op.drop_column("access_packages", "default_approver_id")
