"""lifecycle_events.privileged_flagged_count - how many of a mover's linked PU/TU account access items were put
under review. Additive.

Revision ID: 0053_lifecycle_privileged_flag
Revises: 0052_lifecycle_event_notified
"""
from alembic import op
import sqlalchemy as sa

revision = "0053_lifecycle_privileged_flag"
down_revision = "0052_lifecycle_event_notified"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("lifecycle_events")}
    if "privileged_flagged_count" not in existing:
        op.add_column("lifecycle_events", sa.Column("privileged_flagged_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("lifecycle_events")}
    if "privileged_flagged_count" in existing:
        op.drop_column("lifecycle_events", "privileged_flagged_count")
