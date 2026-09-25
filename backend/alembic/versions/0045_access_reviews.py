"""Access Review campaigns: periodic access recertification. Two new tables, nothing existing touched. See
app.models.models.AccessReviewCampaign / AccessReviewItem and app.services.access_reviews.

Revision ID: 0045_access_reviews
Revises: 0044_user_hierarchy
"""
from alembic import op
import sqlalchemy as sa

revision = "0045_access_reviews"
down_revision = "0044_user_hierarchy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "access_review_campaigns" not in existing_tables:
        op.create_table(
            "access_review_campaigns",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("scope_type", sa.String(30), nullable=False),
            sa.Column("scope_resource_type", sa.String(50), nullable=True),
            sa.Column("scope_resource_id", sa.Uuid(), nullable=True),
            sa.Column("scope_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("scope_account_type", sa.String(20), nullable=True),
            sa.Column("reviewer_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("fallback_reviewer_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("fallback_unlock_hours", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
            sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_access_review_campaigns_status", "access_review_campaigns", ["status"])
    if "access_review_items" not in existing_tables:
        op.create_table(
            "access_review_items",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("campaign_id", sa.Uuid(), sa.ForeignKey("access_review_campaigns.id"), nullable=False),
            sa.Column("assignment_id", sa.Uuid(), sa.ForeignKey("access_assignments.id"), nullable=False),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("resource_type", sa.String(50), nullable=False),
            sa.Column("resource_id", sa.Uuid(), nullable=False),
            sa.Column("app_role_external_id", sa.String(100), nullable=True),
            sa.Column("assignment_status_at_snapshot", sa.String(50), nullable=False),
            sa.Column("decision", sa.String(20), nullable=False, server_default="PENDING"),
            sa.Column("decided_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("justification", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_access_review_items_campaign", "access_review_items", ["campaign_id"])
        op.create_index("ix_access_review_items_decision", "access_review_items", ["decision"])


def downgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "access_review_items" in existing_tables:
        op.drop_table("access_review_items")
    if "access_review_campaigns" in existing_tables:
        op.drop_table("access_review_campaigns")
