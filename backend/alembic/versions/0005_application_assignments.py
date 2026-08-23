"""Add applications table (Entra Enterprise Applications + their app roles) and let assignments target a specific app role.

Revision ID: 0005_application_assignments
Revises: 0004_provider_sync_schedule
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_application_assignments"
down_revision = "0004_provider_sync_schedule"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "applications" not in inspector.get_table_names():
        op.create_table(
            "applications",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("provider_id", sa.Uuid(), sa.ForeignKey("identity_providers.id"), nullable=False),
            sa.Column("external_id", sa.String(length=255), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("status", sa.String(length=50), nullable=False),
            sa.Column("app_roles", sa.JSON(), nullable=True),
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("provider_id", "external_id", name="uq_applications_provider_external"),
        )
        op.create_index("ix_applications_provider_external", "applications", ["provider_id", "external_id"])

    existing_columns = {column["name"] for column in inspector.get_columns("access_assignments")}
    if "app_role_external_id" not in existing_columns:
        op.add_column("access_assignments", sa.Column("app_role_external_id", sa.String(length=100), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_columns = {column["name"] for column in inspector.get_columns("access_assignments")}
    if "app_role_external_id" in existing_columns:
        op.drop_column("access_assignments", "app_role_external_id")
    if "applications" in inspector.get_table_names():
        op.drop_index("ix_applications_provider_external", table_name="applications")
        op.drop_table("applications")
