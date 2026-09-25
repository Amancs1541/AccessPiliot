"""Access Review campaigns can now scope to multiple specific resources at once (mixing Groups, Roles,
Applications, and Packages freely in one campaign) — a new nullable scope_targets JSON column, used only when
scope_type is MULTIPLE_RESOURCES. See app.models.models.AccessReviewCampaign / app.services.access_reviews.

Revision ID: 0046_access_review_multi_scope
Revises: 0045_access_reviews
"""
from alembic import op
import sqlalchemy as sa

revision = "0046_access_review_multi_scope"
down_revision = "0045_access_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("access_review_campaigns")}
    if "scope_targets" not in columns:
        op.add_column("access_review_campaigns", sa.Column("scope_targets", sa.JSON(), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("access_review_campaigns")}
    if "scope_targets" in columns:
        op.drop_column("access_review_campaigns", "scope_targets")
