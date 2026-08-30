"""Add session_secret to breakglass_accounts — Phase 12 step 2: break-glass login issues an HS256 session token
signed with this account-specific secret, verified as an additive fallback path alongside (never instead of)
normal Entra/IDP token validation.

Revision ID: 0019_breakglass_secret
Revises: 0018_portal_authn
"""
from alembic import op
import sqlalchemy as sa

revision = "0019_breakglass_secret"
down_revision = "0018_portal_authn"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("breakglass_accounts")}
    if "session_secret" not in existing:
        op.add_column("breakglass_accounts", sa.Column("session_secret", sa.String(255), nullable=True))


def downgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("breakglass_accounts")}
    if "session_secret" in existing:
        op.drop_column("breakglass_accounts", "session_secret")
