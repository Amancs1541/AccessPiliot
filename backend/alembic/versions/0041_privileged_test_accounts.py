"""Privileged (PU) / Test (TU) accounts: account_type + linked_user_id on users, plus new
privileged_account_policies (per-account-type approval config) and privileged_account_requests tables. See
app.services.privileged_accounts.

Revision ID: 0041_privileged_test_accounts
Revises: 0040_group_role_mappings
"""
from alembic import op
import sqlalchemy as sa

revision = "0041_privileged_test_accounts"
down_revision = "0040_group_role_mappings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "account_type" not in columns:
        op.add_column("users", sa.Column("account_type", sa.String(20), nullable=False, server_default="NORMAL"))
    if "linked_user_id" not in columns:
        op.add_column("users", sa.Column("linked_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True))

    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "privileged_account_policies" not in existing_tables:
        op.create_table(
            "privileged_account_policies",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("account_type", sa.String(20), nullable=False, unique=True),
            sa.Column("default_approver_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    if "privileged_account_requests" not in existing_tables:
        op.create_table(
            "privileged_account_requests",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("requester_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("account_type", sa.String(20), nullable=False),
            sa.Column("status", sa.String(50), nullable=False, server_default="PENDING_APPROVAL"),
            sa.Column("approver_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("justification", sa.Text(), nullable=True),
            sa.Column("provisioned_user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("failure_reason", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_privileged_account_requests_requester", "privileged_account_requests", ["requester_id"])


def downgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "privileged_account_requests" in existing_tables:
        op.drop_table("privileged_account_requests")
    if "privileged_account_policies" in existing_tables:
        op.drop_table("privileged_account_policies")
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "linked_user_id" in columns:
        op.drop_column("users", "linked_user_id")
    if "account_type" in columns:
        op.drop_column("users", "account_type")
