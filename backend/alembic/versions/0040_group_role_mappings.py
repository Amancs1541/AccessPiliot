"""Add group_role_mappings — the group-membership analog of birthright_policies: everyone currently in a given
group is entitled to a Role or Application(+app role), reconciled the same safe way birthright grants are (see
app.services.group_role_mapping). Also adds group_role_mapping_id to access_assignments, mirroring
birthright_policy_id.

Revision ID: 0040_group_role_mappings
Revises: 0039_backfill_birthright_links
"""
from alembic import op
import sqlalchemy as sa

revision = "0040_group_role_mappings"
down_revision = "0039_backfill_birthright_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "group_role_mappings" not in existing_tables:
        op.create_table(
            "group_role_mappings",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("source_group_id", sa.Uuid(), sa.ForeignKey("groups.id"), nullable=False),
            sa.Column("resource_type", sa.String(50), nullable=False),
            sa.Column("resource_id", sa.Uuid(), nullable=False),
            sa.Column("app_role_external_id", sa.String(100), nullable=True),
            sa.Column("assignment_type", sa.String(50), nullable=False, server_default="PERMANENT"),
            sa.Column("status", sa.String(50), nullable=False, server_default="ACTIVE"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_group_role_mappings_source_group", "group_role_mappings", ["source_group_id"])

    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("access_assignments")}
    if "group_role_mapping_id" not in columns:
        op.add_column("access_assignments", sa.Column("group_role_mapping_id", sa.Uuid(), sa.ForeignKey("group_role_mappings.id"), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("access_assignments")}
    if "group_role_mapping_id" in columns:
        op.drop_column("access_assignments", "group_role_mapping_id")
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "group_role_mappings" in existing_tables:
        op.drop_table("group_role_mappings")
