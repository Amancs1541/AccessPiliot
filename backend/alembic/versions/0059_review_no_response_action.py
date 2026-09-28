"""Access review campaigns: what happens to items nobody decided by the due date (REVOKE = existing behaviour,
KEEP = auto-approve). Additive; existing rows default to REVOKE.

Revision ID: 0059_review_no_response
Revises: 0058_joiner_requests
"""
from alembic import op
import sqlalchemy as sa

revision = "0059_review_no_response"
down_revision = "0058_joiner_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("access_review_campaigns")}
    if "on_no_response" not in columns:
        op.add_column("access_review_campaigns", sa.Column("on_no_response", sa.String(10), nullable=False, server_default="REVOKE"))


def downgrade() -> None:
    op.drop_column("access_review_campaigns", "on_no_response")
