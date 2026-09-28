"""Leaver policies (+ a seeded Default) and the per-person leaver bookkeeping columns
(users.leaver_processed_at / leaver_reminders_sent). Additive.

Revision ID: 0057_leaver_policies
Revises: 0056_identity_accounts
"""
import uuid

from alembic import op
import sqlalchemy as sa

revision = "0057_leaver_policies"
down_revision = "0056_identity_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    if "leaver_processed_at" not in user_columns:
        op.add_column("users", sa.Column("leaver_processed_at", sa.DateTime(timezone=True), nullable=True))
    if "leaver_reminders_sent" not in user_columns:
        op.add_column("users", sa.Column("leaver_reminders_sent", sa.JSON(), nullable=True))
    if "leaver_policies" not in inspector.get_table_names():
        op.create_table(
            "leaver_policies",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("name", sa.String(255), nullable=False, unique=True),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("scope_type", sa.String(20), nullable=False, server_default="ALL"),
            sa.Column("scope_value", sa.String(200), nullable=True),
            sa.Column("effective_time", sa.String(5), nullable=False, server_default="23:59"),
            sa.Column("notify_days_before", sa.JSON(), nullable=True),
            sa.Column("revoke_access", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("disable_accounts", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("disable_privileged_accounts", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("remove_group_memberships", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    exists = bind.execute(sa.text("SELECT 1 FROM leaver_policies WHERE is_default = :d LIMIT 1"), {"d": True}).first()
    if exists is None:
        table = sa.table("leaver_policies", sa.column("id", sa.Uuid()), sa.column("name", sa.String()), sa.column("priority", sa.Integer()), sa.column("scope_type", sa.String()), sa.column("notify_days_before", sa.JSON()), sa.column("is_default", sa.Boolean()))
        op.bulk_insert(table, [{"id": uuid.uuid4(), "name": "Default leaver policy", "priority": 1000, "scope_type": "ALL", "notify_days_before": [7, 1], "is_default": True}])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "leaver_policies" in inspector.get_table_names():
        op.drop_table("leaver_policies")
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    for name in ("leaver_reminders_sent", "leaver_processed_at"):
        if name in user_columns:
            op.drop_column("users", name)
