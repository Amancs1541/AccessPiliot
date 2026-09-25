"""Birthright policy rule engine: relax match_field/match_value/resource_type/resource_id to nullable (a
policy is now either the original single-condition/single-grant shape OR the new JSON-authored multi-condition/
multi-action shape, never both), plus new columns external_policy_id, scope_identity_type, conditions_json,
conditions_operator, actions_json, reconciliation_enabled. See app.models.models.BirthrightPolicy and
app.services.birthright.

Revision ID: 0042_birthright_rule_engine
Revises: 0041_privileged_test_accounts
"""
from alembic import op
import sqlalchemy as sa

revision = "0042_birthright_rule_engine"
down_revision = "0041_privileged_test_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"]: column for column in sa.inspect(op.get_bind()).get_columns("birthright_policies")}
    if not columns["match_field"]["nullable"]:
        op.alter_column("birthright_policies", "match_field", existing_type=sa.String(50), nullable=True)
    if not columns["match_value"]["nullable"]:
        op.alter_column("birthright_policies", "match_value", existing_type=sa.String(255), nullable=True)
    if not columns["resource_type"]["nullable"]:
        op.alter_column("birthright_policies", "resource_type", existing_type=sa.String(50), nullable=True)
    if not columns["resource_id"]["nullable"]:
        op.alter_column("birthright_policies", "resource_id", existing_type=sa.Uuid(), nullable=True)
    if "external_policy_id" not in columns:
        op.add_column("birthright_policies", sa.Column("external_policy_id", sa.String(100), nullable=True))
        op.create_unique_constraint("uq_birthright_policies_external_policy_id", "birthright_policies", ["external_policy_id"])
    if "scope_identity_type" not in columns:
        op.add_column("birthright_policies", sa.Column("scope_identity_type", sa.String(50), nullable=True))
    if "conditions_json" not in columns:
        op.add_column("birthright_policies", sa.Column("conditions_json", sa.JSON(), nullable=True))
    if "conditions_operator" not in columns:
        op.add_column("birthright_policies", sa.Column("conditions_operator", sa.String(10), nullable=False, server_default="AND"))
    if "actions_json" not in columns:
        op.add_column("birthright_policies", sa.Column("actions_json", sa.JSON(), nullable=True))
    if "reconciliation_enabled" not in columns:
        op.add_column("birthright_policies", sa.Column("reconciliation_enabled", sa.Boolean(), nullable=False, server_default="true"))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("birthright_policies")}
    for name in ("reconciliation_enabled", "actions_json", "conditions_operator", "conditions_json", "scope_identity_type"):
        if name in columns:
            op.drop_column("birthright_policies", name)
    if "external_policy_id" in columns:
        op.drop_constraint("uq_birthright_policies_external_policy_id", "birthright_policies", type_="unique")
        op.drop_column("birthright_policies", "external_policy_id")
