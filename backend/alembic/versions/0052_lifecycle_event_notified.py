"""lifecycle_events.notified_user_ids - who was notified about the event, so the Movers report can show it. Additive.

Revision ID: 0052_lifecycle_event_notified
Revises: 0051_lifecycle_events_settings
"""
from alembic import op
import sqlalchemy as sa

revision = "0052_lifecycle_event_notified"
down_revision = "0051_lifecycle_events_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("lifecycle_events")}
    if "notified_user_ids" not in existing:
        op.add_column("lifecycle_events", sa.Column("notified_user_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("lifecycle_events")}
    if "notified_user_ids" in existing:
        op.drop_column("lifecycle_events", "notified_user_ids")
