"""Manual 'Start leaver process' now goes through justification -> accounts disabled -> manager approval. Additive.

Revision ID: 0061_leaver_requests
Revises: 0060_reenable_and_deletion
"""
from alembic import op
import sqlalchemy as sa

revision = "0061_leaver_requests"
down_revision = "0060_reenable_and_deletion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "leaver_requests" not in inspector.get_table_names():
        op.create_table(
            "leaver_requests",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("justification", sa.String(1000), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
            sa.Column("approver_ids", sa.JSON(), nullable=True),
            sa.Column("disabled_accounts", sa.JSON(), nullable=True),
            sa.Column("decided_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("decision_note", sa.String(1000), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("outcome", sa.String(500), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_leaver_requests_user_status", "leaver_requests", ["user_id", "status"])


def downgrade() -> None:
    if "leaver_requests" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("leaver_requests")
