"""Add security_settings.logout_enabled / logout_after_minutes — a third, independent idle-session tier where the
user is actually signed out (unlike blur/lock, which never end the session).

Revision ID: 0023_security_settings_logout
Revises: 0022_branding_settings
"""
from alembic import op
import sqlalchemy as sa

revision = "0023_security_settings_logout"
down_revision = "0022_branding_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("security_settings")}
    if "logout_enabled" not in columns:
        op.add_column("security_settings", sa.Column("logout_enabled", sa.Boolean(), nullable=False, server_default="false"))
    if "logout_after_minutes" not in columns:
        op.add_column("security_settings", sa.Column("logout_after_minutes", sa.Integer(), nullable=False, server_default="15"))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("security_settings")}
    if "logout_after_minutes" in columns:
        op.drop_column("security_settings", "logout_after_minutes")
    if "logout_enabled" in columns:
        op.drop_column("security_settings", "logout_enabled")
