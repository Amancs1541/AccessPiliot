"""Links AccessAssignment to the Workflow Engine: a nullable workflow_instance_id provenance column, the same
pattern as birthright_policy_id/business_role_id — set only when the assignment's approval is routed through a
Workflow instead of a single approver. Purely additive; no existing column changes.

Revision ID: 0066_assignment_workflow_link
Revises: 0065_workflow_engine
"""
from alembic import op
import sqlalchemy as sa

revision = "0066_assignment_workflow_link"
down_revision = "0065_workflow_engine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("access_assignments")}
    if "workflow_instance_id" not in columns:
        op.add_column("access_assignments", sa.Column("workflow_instance_id", sa.Uuid(), sa.ForeignKey("workflow_instances.id"), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("access_assignments")}
    if "workflow_instance_id" in columns:
        op.drop_column("access_assignments", "workflow_instance_id")
