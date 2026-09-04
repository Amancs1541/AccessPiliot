"""Add sod_exception_requests (the "hit a block, ask for an exception" workflow) and two new columns on
sod_notification_settings (cooldown_enabled/cooldown_hours) — the anti-gaming measure for deactivate-then-
activate-the-conflicting-side cycling.

Revision ID: 0027_sod_exception_requests
Revises: 0026_sod_notifications
"""
from alembic import op
import sqlalchemy as sa

revision = "0027_sod_exception_requests"
down_revision = "0026_sod_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("sod_notification_settings")}
    if "cooldown_enabled" not in columns:
        op.add_column("sod_notification_settings", sa.Column("cooldown_enabled", sa.Boolean(), nullable=False, server_default="false"))
    if "cooldown_hours" not in columns:
        op.add_column("sod_notification_settings", sa.Column("cooldown_hours", sa.Integer(), nullable=False, server_default="24"))

    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "sod_exception_requests" not in existing_tables:
        op.create_table(
            "sod_exception_requests",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("sod_policy_id", sa.Uuid(), sa.ForeignKey("sod_policies.id"), nullable=False),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("justification", sa.Text(), nullable=False),
            sa.Column("resource_type", sa.String(50), nullable=False),
            sa.Column("resource_id", sa.Uuid(), nullable=False),
            sa.Column("app_role_external_id", sa.String(100), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
            sa.Column("decided_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("denial_reason", sa.Text(), nullable=True),
            sa.Column("sod_exception_id", sa.Uuid(), sa.ForeignKey("sod_exceptions.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )

    notification_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("sod_notifications")}
    if "sod_exception_request_id" not in notification_columns:
        op.add_column("sod_notifications", sa.Column("sod_exception_request_id", sa.Uuid(), sa.ForeignKey("sod_exception_requests.id"), nullable=True))


def downgrade() -> None:
    notification_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("sod_notifications")}
    if "sod_exception_request_id" in notification_columns:
        op.drop_column("sod_notifications", "sod_exception_request_id")

    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "sod_exception_requests" in existing_tables:
        op.drop_table("sod_exception_requests")

    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("sod_notification_settings")}
    if "cooldown_hours" in columns:
        op.drop_column("sod_notification_settings", "cooldown_hours")
    if "cooldown_enabled" in columns:
        op.drop_column("sod_notification_settings", "cooldown_enabled")
