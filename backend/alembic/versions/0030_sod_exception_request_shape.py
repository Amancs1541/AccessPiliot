"""Add approver_id/fallback_approver_id/fallback_unlock_hours/assignment_type/expiration_time to
sod_exception_requests — enough of the original blocked AssignmentCreate's shape that granting can recreate it
faithfully (including routing through the same approver) instead of only ever landing on a bare ELIGIBLE row.

Revision ID: 0030_sod_exc_req_shape
Revises: 0029_notifications
"""
from alembic import op
import sqlalchemy as sa

revision = "0030_sod_exc_req_shape"
down_revision = "0029_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("sod_exception_requests")}
    if "approver_id" not in columns:
        op.add_column("sod_exception_requests", sa.Column("approver_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True))
    if "fallback_approver_id" not in columns:
        op.add_column("sod_exception_requests", sa.Column("fallback_approver_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True))
    if "fallback_unlock_hours" not in columns:
        op.add_column("sod_exception_requests", sa.Column("fallback_unlock_hours", sa.Integer(), nullable=True))
    if "assignment_type" not in columns:
        op.add_column("sod_exception_requests", sa.Column("assignment_type", sa.String(50), nullable=False, server_default="PERMANENT"))
    if "expiration_time" not in columns:
        op.add_column("sod_exception_requests", sa.Column("expiration_time", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("sod_exception_requests")}
    for column in ("expiration_time", "assignment_type", "fallback_unlock_hours", "fallback_approver_id", "approver_id"):
        if column in columns:
            op.drop_column("sod_exception_requests", column)
