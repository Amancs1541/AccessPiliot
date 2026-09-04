"""Add notify_on_exception_requested to sod_notification_settings — a dedicated on/off switch for the
EXCEPTION_REQUESTED notification (created eagerly at request time, not via the reconciliation pass), matching
the two existing reconciliation-driven toggles.

Revision ID: 0028_sod_notify_toggle
Revises: 0027_sod_exception_requests
"""
from alembic import op
import sqlalchemy as sa

revision = "0028_sod_notify_toggle"
down_revision = "0027_sod_exception_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("sod_notification_settings")}
    if "notify_on_exception_requested" not in columns:
        op.add_column("sod_notification_settings", sa.Column("notify_on_exception_requested", sa.Boolean(), nullable=False, server_default="true"))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("sod_notification_settings")}
    if "notify_on_exception_requested" in columns:
        op.drop_column("sod_notification_settings", "notify_on_exception_requested")
