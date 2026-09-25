"""Access Review campaigns gain recurrence (frequency_days + parent_campaign_id, for auto-recreating the next
campaign when one completes) and an INACTIVE_USERS scope type (scope_inactive_days threshold). Fully additive —
nullable columns only, no existing data touched. See app.models.models.AccessReviewCampaign /
app.services.access_reviews.

Revision ID: 0047_access_review_recurrence
Revises: 0046_access_review_multi_scope
"""
from alembic import op
import sqlalchemy as sa

revision = "0047_access_review_recurrence"
down_revision = "0046_access_review_multi_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("access_review_campaigns")}
    if "scope_inactive_days" not in columns:
        op.add_column("access_review_campaigns", sa.Column("scope_inactive_days", sa.Integer(), nullable=True))
    if "frequency_days" not in columns:
        op.add_column("access_review_campaigns", sa.Column("frequency_days", sa.Integer(), nullable=True))
    if "parent_campaign_id" not in columns:
        op.add_column("access_review_campaigns", sa.Column("parent_campaign_id", sa.Uuid(), nullable=True))
        op.create_foreign_key("fk_access_review_campaigns_parent", "access_review_campaigns", "access_review_campaigns", ["parent_campaign_id"], ["id"])


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("access_review_campaigns")}
    if "parent_campaign_id" in columns:
        op.drop_constraint("fk_access_review_campaigns_parent", "access_review_campaigns", type_="foreignkey")
        op.drop_column("access_review_campaigns", "parent_campaign_id")
    if "frequency_days" in columns:
        op.drop_column("access_review_campaigns", "frequency_days")
    if "scope_inactive_days" in columns:
        op.drop_column("access_review_campaigns", "scope_inactive_days")
