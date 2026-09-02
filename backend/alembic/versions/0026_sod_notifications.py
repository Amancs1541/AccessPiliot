"""Add sod_notification_settings (singleton, admin-configurable) and sod_notifications (the log) — closes the
"no notifications" gap: a new violation or a soon-expiring exception was previously discoverable only by
someone opening the Dashboard/SoD page.

Revision ID: 0026_sod_notifications
Revises: 0025_sod_exceptions
"""
from alembic import op
import sqlalchemy as sa

revision = "0026_sod_notifications"
down_revision = "0025_sod_exceptions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "sod_notification_settings" not in existing_tables:
        op.create_table(
            "sod_notification_settings",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("notify_on_new_violation", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("notify_on_exception_expiring", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("exception_expiring_warning_days", sa.Integer(), nullable=False, server_default="7"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    if "sod_notifications" not in existing_tables:
        op.create_table(
            "sod_notifications",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("notification_type", sa.String(50), nullable=False),
            sa.Column("sod_policy_id", sa.Uuid(), sa.ForeignKey("sod_policies.id"), nullable=True),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("sod_exception_id", sa.Uuid(), sa.ForeignKey("sod_exceptions.id"), nullable=True),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_sod_notifications_open", "sod_notifications", ["notification_type", "sod_policy_id", "user_id", "resolved_at"])


def downgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "sod_notifications" in existing_tables:
        op.drop_table("sod_notifications")
    if "sod_notification_settings" in existing_tables:
        op.drop_table("sod_notification_settings")
