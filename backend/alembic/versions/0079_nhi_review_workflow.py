"""Workflow integration for NHI Ownership Reviews: nhi_review_campaigns.reviewer_id becomes nullable (now
mutually exclusive with the new workflow_definition_id, same reviewer-xor-workflow shape
access_review_campaigns already has), and nhi_review_items gains workflow_instance_id (one independent
WorkflowInstance per item when the owning campaign is workflow-routed). Entirely additive/widening — no data
loss, every existing reviewer-routed campaign/item is unaffected.

Revision ID: 0079_nhi_review_workflow
Revises: 0078_nhi_management_extensions
"""
from alembic import op
import sqlalchemy as sa

revision = "0079_nhi_review_workflow"
down_revision = "0078_nhi_management_extensions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    campaign_columns = {c["name"] for c in inspector.get_columns("nhi_review_campaigns")}
    item_columns = {c["name"] for c in inspector.get_columns("nhi_review_items")}

    if "workflow_definition_id" not in campaign_columns:
        op.add_column("nhi_review_campaigns", sa.Column("workflow_definition_id", sa.Uuid(), sa.ForeignKey("workflow_definitions.id"), nullable=True))
    # Widen reviewer_id to nullable — a workflow-routed campaign has no single reviewer slot.
    op.alter_column("nhi_review_campaigns", "reviewer_id", existing_type=sa.Uuid(), nullable=True)

    if "workflow_instance_id" not in item_columns:
        op.add_column("nhi_review_items", sa.Column("workflow_instance_id", sa.Uuid(), sa.ForeignKey("workflow_instances.id"), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    campaign_columns = {c["name"] for c in inspector.get_columns("nhi_review_campaigns")}
    item_columns = {c["name"] for c in inspector.get_columns("nhi_review_items")}

    if "workflow_instance_id" in item_columns:
        op.drop_column("nhi_review_items", "workflow_instance_id")
    op.alter_column("nhi_review_campaigns", "reviewer_id", existing_type=sa.Uuid(), nullable=False)
    if "workflow_definition_id" in campaign_columns:
        op.drop_column("nhi_review_campaigns", "workflow_definition_id")
