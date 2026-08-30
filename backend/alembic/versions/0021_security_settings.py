"""Add security_settings — a singleton row governing idle-session blur/lock behavior for every signed-in user.

Revision ID: 0021_security_settings
Revises: 0020_breakglass_emergency
"""
from alembic import op
import sqlalchemy as sa

revision = "0021_security_settings"
down_revision = "0020_breakglass_emergency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "security_settings" not in existing_tables:
        op.create_table(
            "security_settings",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("blur_enabled", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("blur_after_minutes", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("lock_enabled", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("lock_after_minutes", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )


def downgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "security_settings" in existing_tables:
        op.drop_table("security_settings")
