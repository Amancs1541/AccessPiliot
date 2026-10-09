"""Workflow / Approval Engine: a new, standalone multi-stage approval capability (workflow_definitions,
workflow_stage_definitions, workflow_instances, workflow_stage_instances, workflow_stage_decisions,
workflow_requests). Entirely additive — no existing table is touched. See app.services.workflows for the engine.

Revision ID: 0065_workflow_engine
Revises: 0064_business_role_assign
"""
from alembic import op
import sqlalchemy as sa

revision = "0065_workflow_engine"
down_revision = "0064_business_role_assign"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_tables = inspector.get_table_names()

    if "workflow_definitions" not in existing_tables:
        op.create_table(
            "workflow_definitions",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("name", sa.String(255), nullable=False, unique=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
            sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )

    if "workflow_stage_definitions" not in existing_tables:
        op.create_table(
            "workflow_stage_definitions",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("workflow_definition_id", sa.Uuid(), sa.ForeignKey("workflow_definitions.id"), nullable=False),
            sa.Column("stage_number", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("approval_mode", sa.String(20), nullable=False, server_default="ANY_OF"),
            sa.Column("approver_user_ids", sa.JSON(), nullable=False),
            sa.Column("fallback_approver_ids", sa.JSON(), nullable=True),
            sa.Column("escalate_after_hours", sa.Integer(), nullable=True),
            sa.Column("condition_field", sa.String(50), nullable=True),
            sa.Column("condition_operator", sa.String(20), nullable=True),
            sa.Column("condition_value", sa.String(255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_workflow_stage_definitions_definition", "workflow_stage_definitions", ["workflow_definition_id"])
        op.create_unique_constraint("uq_workflow_stage_definitions_number", "workflow_stage_definitions", ["workflow_definition_id", "stage_number"])

    if "workflow_instances" not in existing_tables:
        op.create_table(
            "workflow_instances",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("workflow_definition_id", sa.Uuid(), sa.ForeignKey("workflow_definitions.id"), nullable=False),
            sa.Column("subject_type", sa.String(50), nullable=False),
            sa.Column("subject_id", sa.Uuid(), nullable=True),
            sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
            sa.Column("current_stage_number", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_workflow_instances_definition", "workflow_instances", ["workflow_definition_id"])
        op.create_index("ix_workflow_instances_status", "workflow_instances", ["status"])

    if "workflow_stage_instances" not in existing_tables:
        op.create_table(
            "workflow_stage_instances",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("workflow_instance_id", sa.Uuid(), sa.ForeignKey("workflow_instances.id"), nullable=False),
            sa.Column("stage_number", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("approval_mode", sa.String(20), nullable=False),
            sa.Column("required_approver_ids", sa.JSON(), nullable=False),
            sa.Column("fallback_approver_ids", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
            sa.Column("escalates_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_workflow_stage_instances_instance", "workflow_stage_instances", ["workflow_instance_id"])
        op.create_index("ix_workflow_stage_instances_status", "workflow_stage_instances", ["status"])
        op.create_index("ix_workflow_stage_instances_escalation", "workflow_stage_instances", ["status", "escalates_at", "escalated_at"])

    if "workflow_stage_decisions" not in existing_tables:
        op.create_table(
            "workflow_stage_decisions",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("workflow_stage_instance_id", sa.Uuid(), sa.ForeignKey("workflow_stage_instances.id"), nullable=False),
            sa.Column("decided_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("decision", sa.String(20), nullable=False),
            sa.Column("justification", sa.Text(), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_workflow_stage_decisions_stage", "workflow_stage_decisions", ["workflow_stage_instance_id"])
        op.create_unique_constraint("uq_workflow_stage_decisions_once", "workflow_stage_decisions", ["workflow_stage_instance_id", "decided_by"])

    if "workflow_requests" not in existing_tables:
        op.create_table(
            "workflow_requests",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("workflow_definition_id", sa.Uuid(), sa.ForeignKey("workflow_definitions.id"), nullable=False),
            sa.Column("workflow_instance_id", sa.Uuid(), sa.ForeignKey("workflow_instances.id"), nullable=False, unique=True),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("justification", sa.Text(), nullable=True),
            sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_workflow_requests_requested_by", "workflow_requests", ["requested_by"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in ("workflow_requests", "workflow_stage_decisions", "workflow_stage_instances", "workflow_instances", "workflow_stage_definitions", "workflow_definitions"):
        if table in inspector.get_table_names():
            op.drop_table(table)
