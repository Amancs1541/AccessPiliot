"""Add support_contact_email to security_settings — shown on the sign-in screen (via a new public endpoint, no
auth required, mirroring branding's own public GET) when the IDP itself can't be reached at all, so a locked-out
user has someone real to contact instead of a dead end.

Revision ID: 0034_security_support_email
Revises: 0033_soc_dashboard
"""
from alembic import op
import sqlalchemy as sa

revision = "0034_security_support_email"
down_revision = "0033_soc_dashboard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("security_settings")}
    if "support_contact_email" not in columns:
        op.add_column("security_settings", sa.Column("support_contact_email", sa.String(255), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("security_settings")}
    if "support_contact_email" in columns:
        op.drop_column("security_settings", "support_contact_email")
