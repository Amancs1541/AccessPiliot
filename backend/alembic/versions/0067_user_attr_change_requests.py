"""Workflow-routing for the "edit identity" attribute change: a new user_attribute_change_requests table holding
a department/job_title edit pending approval. Entirely additive — no existing table is touched.

Revision ID: 0067_user_attr_change_requests
Revises: 0066_assignment_workflow_link
"""
from alembic import op
import sqlalchemy as sa

revision = "0067_user_attr_change_requests"
down_revision = "0066_assignment_workflow_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_tables = inspector.get_table_names()

    if "user_attribute_change_requests" not in existing_tables:
        op.create_table(
            "user_attribute_change_requests",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("previous_department", sa.String(200), nullable=True),
            sa.Column("previous_job_title", sa.String(200), nullable=True),
            sa.Column("requested_department", sa.String(200), nullable=True),
            sa.Column("requested_job_title", sa.String(200), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
            sa.Column("workflow_instance_id", sa.Uuid(), sa.ForeignKey("workflow_instances.id"), nullable=True),
            sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_user_attribute_change_requests_user_status", "user_attribute_change_requests", ["user_id", "status"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "user_attribute_change_requests" in inspector.get_table_names():
        op.drop_table("user_attribute_change_requests")
