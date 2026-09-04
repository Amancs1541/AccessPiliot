"""Add timezone to security_settings — the one tenant-wide display timezone every signed-in user's browser reads
(GET /security-settings is open to any authenticated user, not just Admin) so every date/time in the app can be
shown uniformly in an admin-configured zone instead of each viewer's own browser-local time.

Revision ID: 0031_security_tz
Revises: 0030_sod_exc_req_shape
"""
from alembic import op
import sqlalchemy as sa

revision = "0031_security_tz"
down_revision = "0030_sod_exc_req_shape"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("security_settings")}
    if "timezone" not in columns:
        op.add_column("security_settings", sa.Column("timezone", sa.String(50), nullable=False, server_default="Europe/Berlin"))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("security_settings")}
    if "timezone" in columns:
        op.drop_column("security_settings", "timezone")
