"""Add emergency_path_token to breakglass_accounts — the hidden-URL secret checked (via secrets.compare_digest)
alongside username/password on every break-glass login, and the only thing the new `python -m app.cli
emergency-url` command ever generates or displays. Nullable: the account is simply unable to log in until an
operator runs that command once — this is intentional, not a bug, and Postgres allows multiple NULLs under a
unique constraint so the existing live row stays valid with no forced backfill.

Revision ID: 0020_breakglass_emergency
Revises: 0019_breakglass_secret
"""
from alembic import op
import sqlalchemy as sa

revision = "0020_breakglass_emergency"
down_revision = "0019_breakglass_secret"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("breakglass_accounts")}
    if "emergency_path_token" not in existing:
        op.add_column("breakglass_accounts", sa.Column("emergency_path_token", sa.String(64), nullable=True))
        op.create_unique_constraint("uq_breakglass_accounts_emergency_path_token", "breakglass_accounts", ["emergency_path_token"])


def downgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("breakglass_accounts")}
    if "emergency_path_token" in existing:
        op.drop_constraint("uq_breakglass_accounts_emergency_path_token", "breakglass_accounts", type_="unique")
        op.drop_column("breakglass_accounts", "emergency_path_token")
