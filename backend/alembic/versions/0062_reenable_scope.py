"""Scope a re-enable request to specific accounts (a single-IdP "Enable" click) instead of always covering every
account (an "Enable in all IdPs" click still leaves this NULL = all). Additive.

Revision ID: 0062_reenable_scope
Revises: 0061_leaver_requests
"""
from alembic import op
import sqlalchemy as sa

revision = "0062_reenable_scope"
down_revision = "0061_leaver_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("reenable_requests")}
    if "account_ids" not in columns:
        op.add_column("reenable_requests", sa.Column("account_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    if "account_ids" in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("reenable_requests")}:
        op.drop_column("reenable_requests", "account_ids")
