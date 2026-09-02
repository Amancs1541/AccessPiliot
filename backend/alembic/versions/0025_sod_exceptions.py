"""Add sod_exceptions — a formally accepted, time-boxed risk-acceptance record for a specific (policy, user)
pair, closing the SoD engine's biggest gap versus mature IGA tools: without this, every conflict is a hard
binary (block forever, or manually re-override every single time) instead of "review once, accept for a
bounded period."

Revision ID: 0025_sod_exceptions
Revises: 0024_sod_engine
"""
from alembic import op
import sqlalchemy as sa

revision = "0025_sod_exceptions"
down_revision = "0024_sod_engine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "sod_exceptions" not in existing_tables:
        op.create_table(
            "sod_exceptions",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("sod_policy_id", sa.Uuid(), sa.ForeignKey("sod_policies.id"), nullable=False),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("justification", sa.Text(), nullable=False),
            sa.Column("granted_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_sod_exceptions_policy_user", "sod_exceptions", ["sod_policy_id", "user_id"])


def downgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "sod_exceptions" in existing_tables:
        op.drop_table("sod_exceptions")
