"""Leaver follow-up: account re-enable requests (reason + manager approval), automatic account deletion after N days
(leaver_policies.delete_after_days, users.accounts_delete_at / accounts_deleted_at). Additive.

Revision ID: 0060_reenable_and_deletion
Revises: 0059_review_no_response
"""
from alembic import op
import sqlalchemy as sa

revision = "0060_reenable_and_deletion"
down_revision = "0059_review_no_response"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    user_columns = {c["name"] for c in inspector.get_columns("users")}
    for name in ("accounts_delete_at", "accounts_deleted_at"):
        if name not in user_columns:
            op.add_column("users", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    policy_columns = {c["name"] for c in inspector.get_columns("leaver_policies")}
    if "delete_after_days" not in policy_columns:
        op.add_column("leaver_policies", sa.Column("delete_after_days", sa.Integer(), nullable=True))
    if "reenable_requests" not in inspector.get_table_names():
        op.create_table(
            "reenable_requests",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("reason", sa.String(1000), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
            sa.Column("approver_ids", sa.JSON(), nullable=True),
            sa.Column("decided_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("decision_note", sa.String(1000), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_reenable_requests_user_status", "reenable_requests", ["user_id", "status"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "reenable_requests" in inspector.get_table_names():
        op.drop_table("reenable_requests")
    if "delete_after_days" in {c["name"] for c in inspector.get_columns("leaver_policies")}:
        op.drop_column("leaver_policies", "delete_after_days")
    for name in ("accounts_delete_at", "accounts_deleted_at"):
        if name in {c["name"] for c in inspector.get_columns("users")}:
            op.drop_column("users", name)
