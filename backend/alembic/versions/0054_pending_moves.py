"""Effective dating: pending_moves (a department/title change scheduled for a future moment) and
onboarding_imports.moves_scheduled_count. Additive.

Revision ID: 0054_pending_moves
Revises: 0053_lifecycle_privileged_flag
"""
from alembic import op
import sqlalchemy as sa

revision = "0054_pending_moves"
down_revision = "0053_lifecycle_privileged_flag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "pending_moves" not in inspector.get_table_names():
        op.create_table(
            "pending_moves",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("new_department", sa.String(255), nullable=True),
            sa.Column("new_job_title", sa.String(255), nullable=True),
            sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("source", sa.String(20), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="SCHEDULED"),
            sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("import_id", sa.Uuid(), nullable=True),
            sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("failure_reason", sa.String(500), nullable=True),
            sa.Column("lifecycle_event_id", sa.Uuid(), sa.ForeignKey("lifecycle_events.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_pending_moves_status_effective", "pending_moves", ["status", "effective_at"])
        op.create_index("ix_pending_moves_user", "pending_moves", ["user_id"])
    existing = {column["name"] for column in inspector.get_columns("onboarding_imports")}
    if "moves_scheduled_count" not in existing:
        op.add_column("onboarding_imports", sa.Column("moves_scheduled_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "pending_moves" in inspector.get_table_names():
        op.drop_table("pending_moves")
    existing = {column["name"] for column in inspector.get_columns("onboarding_imports")}
    if "moves_scheduled_count" in existing:
        op.drop_column("onboarding_imports", "moves_scheduled_count")
