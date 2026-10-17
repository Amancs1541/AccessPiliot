"""Dynamic Groups v2: a native, first-class AccessPilot group whose membership is computed by its own rule
engine (reuses birthright.py's condition evaluator) rather than attaching a rule to an already-existing group.
dynamic_groups + dynamic_group_targets (one row per connected provider, auto-created OFF, independently toggled
to ELIGIBLE/ACTIVE — creating the real group the first time it moves off OFF). access_assignments gains a
dynamic_group_id provenance FK, same convention as birthright_policy_id. Entirely additive.

Revision ID: 0080_dynamic_groups
Revises: 0079_nhi_review_workflow
"""
from alembic import op
import sqlalchemy as sa

revision = "0080_dynamic_groups"
down_revision = "0079_nhi_review_workflow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "dynamic_groups" not in existing_tables:
        op.create_table(
            "dynamic_groups",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("name", sa.String(255), nullable=False, unique=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("group_label", sa.String(100), nullable=True),
            sa.Column("conditions_json", sa.JSON(), nullable=False),
            sa.Column("conditions_operator", sa.String(10), nullable=False, server_default="AND"),
            sa.Column("status", sa.String(50), nullable=False, server_default="ACTIVE"),
            sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )

    if "dynamic_group_targets" not in existing_tables:
        op.create_table(
            "dynamic_group_targets",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("dynamic_group_id", sa.Uuid(), sa.ForeignKey("dynamic_groups.id", ondelete="CASCADE"), nullable=False),
            sa.Column("provider_id", sa.Uuid(), sa.ForeignKey("identity_providers.id"), nullable=False),
            sa.Column("sync_mode", sa.String(20), nullable=False, server_default="OFF"),
            sa.Column("group_id", sa.Uuid(), sa.ForeignKey("groups.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("dynamic_group_id", "provider_id", name="uq_dynamic_group_targets_group_provider"),
        )

    assignment_columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("access_assignments")}
    if "dynamic_group_id" not in assignment_columns:
        op.add_column("access_assignments", sa.Column("dynamic_group_id", sa.Uuid(), sa.ForeignKey("dynamic_groups.id"), nullable=True))


def downgrade() -> None:
    assignment_columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("access_assignments")}
    if "dynamic_group_id" in assignment_columns:
        op.drop_column("access_assignments", "dynamic_group_id")

    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "dynamic_group_targets" in existing_tables:
        op.drop_table("dynamic_group_targets")
    if "dynamic_groups" in existing_tables:
        op.drop_table("dynamic_groups")
