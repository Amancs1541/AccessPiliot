"""Add branding_settings — a singleton row for the customizable sign-in logo, internal sidebar logo, and
"Powered by" attribution text.

Revision ID: 0022_branding_settings
Revises: 0021_security_settings
"""
from alembic import op
import sqlalchemy as sa

revision = "0022_branding_settings"
down_revision = "0021_security_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "branding_settings" not in existing_tables:
        op.create_table(
            "branding_settings",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("sign_in_logo", sa.Text(), nullable=True),
            sa.Column("internal_logo", sa.Text(), nullable=True),
            sa.Column("powered_by_text", sa.String(100), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )


def downgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "branding_settings" in existing_tables:
        op.drop_table("branding_settings")
