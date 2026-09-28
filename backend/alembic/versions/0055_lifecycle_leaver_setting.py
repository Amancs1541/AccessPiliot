"""lifecycle_settings.revoke_on_directory_disable - revoke a person's access when directory sync sees them disabled.
Additive; defaults to true.

Revision ID: 0055_lifecycle_leaver_setting
Revises: 0054_pending_moves
"""
from alembic import op
import sqlalchemy as sa

revision = "0055_lifecycle_leaver_setting"
down_revision = "0054_pending_moves"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("lifecycle_settings")}
    if "revoke_on_directory_disable" not in existing:
        op.add_column("lifecycle_settings", sa.Column("revoke_on_directory_disable", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("lifecycle_settings")}
    if "revoke_on_directory_disable" in existing:
        op.drop_column("lifecycle_settings", "revoke_on_directory_disable")
