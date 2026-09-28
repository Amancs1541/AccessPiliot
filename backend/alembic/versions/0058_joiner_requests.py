"""Joiner process: joiner_requests and identity_providers.provision_joiners (per-IdP "auto-create accounts for
joiners" switch, default on). Additive.

Revision ID: 0058_joiner_requests
Revises: 0057_leaver_policies
"""
from alembic import op
import sqlalchemy as sa

revision = "0058_joiner_requests"
down_revision = "0057_leaver_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    provider_columns = {column["name"] for column in inspector.get_columns("identity_providers")}
    if "provision_joiners" not in provider_columns:
        op.add_column("identity_providers", sa.Column("provision_joiners", sa.Boolean(), nullable=False, server_default=sa.true()))
    if "joiner_requests" not in inspector.get_table_names():
        op.create_table(
            "joiner_requests",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("first_name", sa.String(120), nullable=False),
            sa.Column("last_name", sa.String(120), nullable=False),
            sa.Column("work_email", sa.String(320), nullable=False),
            sa.Column("employee_id", sa.String(100), nullable=True),
            sa.Column("department", sa.String(200), nullable=True),
            sa.Column("job_title", sa.String(200), nullable=True),
            sa.Column("manager_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("employee_category", sa.String(20), nullable=True),
            sa.Column("employment_type", sa.String(20), nullable=True),
            sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("leaver_date", sa.Date(), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="SCHEDULED"),
            sa.Column("targets", sa.JSON(), nullable=True),
            sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_joiner_requests_status_start", "joiner_requests", ["status", "start_at"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "joiner_requests" in inspector.get_table_names():
        op.drop_table("joiner_requests")
    provider_columns = {column["name"] for column in inspector.get_columns("identity_providers")}
    if "provision_joiners" in provider_columns:
        op.drop_column("identity_providers", "provision_joiners")
