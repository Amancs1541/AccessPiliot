"""Complete NHI management: nhi_notification_settings (singleton, admin-configurable) and nhi_notifications (the
shared log) close the "no notifications" gap for NHI risk, same reasoning as 0026_sod_notifications did for SoD.
nhi_review_campaigns and nhi_review_items are a small, parallel model to access_review_campaigns/
access_review_items — NOT a fork of it: AccessReviewItem.assignment_id is a required FK representing "access to
revoke," which doesn't fit "ownership to confirm" (see app.services.nhi_reviews). Entirely additive.

Revision ID: 0078_nhi_management_extensions
Revises: 0077_secondary_retry_sweep
"""
from alembic import op
import sqlalchemy as sa

revision = "0078_nhi_management_extensions"
down_revision = "0077_secondary_retry_sweep"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "nhi_notification_settings" not in existing_tables:
        op.create_table(
            "nhi_notification_settings",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("notify_on_no_owner", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("notify_on_credential_expiring", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("credential_expiring_warning_days", sa.Integer(), nullable=False, server_default="30"),
            sa.Column("notify_on_credential_expired", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )

    if "nhi_notifications" not in existing_tables:
        op.create_table(
            "nhi_notifications",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("notification_type", sa.String(50), nullable=False),
            sa.Column("application_id", sa.Uuid(), sa.ForeignKey("applications.id"), nullable=False),
            sa.Column("owner_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_nhi_notifications_open", "nhi_notifications", ["notification_type", "application_id", "owner_user_id", "resolved_at"])

    if "nhi_review_campaigns" not in existing_tables:
        op.create_table(
            "nhi_review_campaigns",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("reviewer_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("fallback_reviewer_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("fallback_unlock_hours", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
            sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("frequency_days", sa.Integer(), nullable=True),
            sa.Column("parent_campaign_id", sa.Uuid(), sa.ForeignKey("nhi_review_campaigns.id"), nullable=True),
            sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_nhi_review_campaigns_status", "nhi_review_campaigns", ["status"])

    if "nhi_review_items" not in existing_tables:
        op.create_table(
            "nhi_review_items",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("campaign_id", sa.Uuid(), sa.ForeignKey("nhi_review_campaigns.id"), nullable=False),
            sa.Column("application_id", sa.Uuid(), sa.ForeignKey("applications.id"), nullable=False),
            sa.Column("owner_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("decision", sa.String(20), nullable=False, server_default="PENDING"),
            sa.Column("decided_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_nhi_review_items_campaign", "nhi_review_items", ["campaign_id"])


def downgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "nhi_review_items" in existing_tables:
        op.drop_table("nhi_review_items")
    if "nhi_review_campaigns" in existing_tables:
        op.drop_table("nhi_review_campaigns")
    if "nhi_notifications" in existing_tables:
        op.drop_table("nhi_notifications")
    if "nhi_notification_settings" in existing_tables:
        op.drop_table("nhi_notification_settings")
