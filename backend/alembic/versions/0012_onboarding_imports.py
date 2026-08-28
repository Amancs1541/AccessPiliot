"""Add onboarding_imports and onboarding_import_records — backs CSV/HR identity onboarding (Phase 6). Purely
additive: no existing table is touched, CSV-sourced identities land in the existing `users` table under a
dedicated CSV provider row, keyed by employeeId as external_id.

Revision ID: 0012_onboarding_imports
Revises: 0011_bypass_activation
"""
from alembic import op
import sqlalchemy as sa

revision = "0012_onboarding_imports"
down_revision = "0011_bypass_activation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "onboarding_imports" not in existing_tables:
        op.create_table(
            "onboarding_imports",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("provider_id", sa.Uuid(), sa.ForeignKey("identity_providers.id"), nullable=False),
            sa.Column("filename", sa.String(255), nullable=False),
            sa.Column("status", sa.String(50), nullable=False),
            sa.Column("total_records", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("disabled_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("no_change_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("uploaded_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("error_summary", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_onboarding_imports_provider", "onboarding_imports", ["provider_id"])

    if "onboarding_import_records" not in existing_tables:
        op.create_table(
            "onboarding_import_records",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("import_id", sa.Uuid(), sa.ForeignKey("onboarding_imports.id"), nullable=False),
            sa.Column("row_number", sa.Integer(), nullable=False),
            sa.Column("employee_id", sa.String(100), nullable=False),
            sa.Column("action", sa.String(20), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("raw_data", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_onboarding_import_records_import", "onboarding_import_records", ["import_id"])


def downgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "onboarding_import_records" in existing_tables:
        op.drop_table("onboarding_import_records")
    if "onboarding_imports" in existing_tables:
        op.drop_table("onboarding_imports")
