"""Workflow-routing for Access Review Campaigns and Access Packages. access_review_campaigns.reviewer_id becomes
nullable (a workflow-routed campaign has no single reviewer) and gains workflow_definition_id;
access_review_items gains workflow_instance_id (one independent instance per item); access_packages gains
workflow_definition_id (the self-service-request default approval method). Entirely additive aside from the
nullability change, which no existing row violates (every campaign ever created so far has a real reviewer_id).

Revision ID: 0068_review_and_package_wf
Revises: 0067_user_attr_change_requests
"""
from alembic import op
import sqlalchemy as sa

revision = "0068_review_and_package_wf"
down_revision = "0067_user_attr_change_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    campaign_columns = {c["name"] for c in inspector.get_columns("access_review_campaigns")}
    if "workflow_definition_id" not in campaign_columns:
        op.add_column("access_review_campaigns", sa.Column("workflow_definition_id", sa.Uuid(), sa.ForeignKey("workflow_definitions.id"), nullable=True))
    reviewer_column = next((c for c in inspector.get_columns("access_review_campaigns") if c["name"] == "reviewer_id"), None)
    if reviewer_column is not None and not reviewer_column["nullable"]:
        op.alter_column("access_review_campaigns", "reviewer_id", nullable=True)

    item_columns = {c["name"] for c in inspector.get_columns("access_review_items")}
    if "workflow_instance_id" not in item_columns:
        op.add_column("access_review_items", sa.Column("workflow_instance_id", sa.Uuid(), sa.ForeignKey("workflow_instances.id"), nullable=True))

    package_columns = {c["name"] for c in inspector.get_columns("access_packages")}
    if "workflow_definition_id" not in package_columns:
        op.add_column("access_packages", sa.Column("workflow_definition_id", sa.Uuid(), sa.ForeignKey("workflow_definitions.id"), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    package_columns = {c["name"] for c in inspector.get_columns("access_packages")}
    if "workflow_definition_id" in package_columns:
        op.drop_column("access_packages", "workflow_definition_id")

    item_columns = {c["name"] for c in inspector.get_columns("access_review_items")}
    if "workflow_instance_id" in item_columns:
        op.drop_column("access_review_items", "workflow_instance_id")

    campaign_columns = {c["name"] for c in inspector.get_columns("access_review_campaigns")}
    reviewer_column = next((c for c in inspector.get_columns("access_review_campaigns") if c["name"] == "reviewer_id"), None)
    if reviewer_column is not None and reviewer_column["nullable"]:
        op.alter_column("access_review_campaigns", "reviewer_id", nullable=False)
    if "workflow_definition_id" in campaign_columns:
        op.drop_column("access_review_campaigns", "workflow_definition_id")
