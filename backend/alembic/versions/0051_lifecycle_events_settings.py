"""JML lifecycle foundation: lifecycle_events (one row per detected joiner/mover/leaver change) and
lifecycle_settings (singleton: mover review on/off, review due days, lifecycle owners). Additive.

Revision ID: 0051_lifecycle_events_settings
Revises: 0050_import_bright_revoked
"""
from alembic import op
import sqlalchemy as sa

revision = "0051_lifecycle_events_settings"
down_revision = "0050_import_bright_revoked"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()
    if "lifecycle_events" not in tables:
        op.create_table(
            "lifecycle_events",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("event_type", sa.String(20), nullable=False),
            sa.Column("source", sa.String(20), nullable=False),
            sa.Column("changes", sa.JSON(), nullable=True),
            sa.Column("revoked_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("granted_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("review_campaign_id", sa.Uuid(), sa.ForeignKey("access_review_campaigns.id"), nullable=True),
            sa.Column("review_note", sa.String(50), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_lifecycle_events_user", "lifecycle_events", ["user_id"])
        op.create_index("ix_lifecycle_events_type_created", "lifecycle_events", ["event_type", "created_at"])
    if "lifecycle_settings" not in tables:
        op.create_table(
            "lifecycle_settings",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("mover_review_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("review_due_days", sa.Integer(), nullable=False, server_default="14"),
            sa.Column("lifecycle_owner_ids", sa.JSON(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )


def downgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()
    if "lifecycle_events" in tables:
        op.drop_table("lifecycle_events")
    if "lifecycle_settings" in tables:
        op.drop_table("lifecycle_settings")
