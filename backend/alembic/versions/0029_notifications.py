"""Add notifications — a general-purpose, per-user notification table (distinct from sod_notifications, which
keeps its own global-read-state model, correct only at small-admin-team scale). Every ordinary end user gets
their own row-per-notification with their own read_at, for assignment/approval lifecycle events.

Revision ID: 0029_notifications
Revises: 0028_sod_notify_toggle
"""
from alembic import op
import sqlalchemy as sa

revision = "0029_notifications"
down_revision = "0028_sod_notify_toggle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "notifications" not in existing_tables:
        op.create_table(
            "notifications",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("notification_type", sa.String(50), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("link", sa.String(255), nullable=True),
            sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_notifications_user_id", "notifications", ["user_id"])


def downgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "notifications" in existing_tables:
        op.drop_index("ix_notifications_user_id", table_name="notifications")
        op.drop_table("notifications")
