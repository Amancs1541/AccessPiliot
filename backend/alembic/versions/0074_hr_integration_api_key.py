"""hr_integration_settings gains auth_method ('SESSION', the existing default, or 'API_KEY', new) and
api_key_encrypted (Fernet-encrypted, same mechanism as password_encrypted) — lets AccessPilot authenticate to a
standalone HR app's API-key-protected endpoints instead of a stored username/password. Entirely additive; every
existing row defaults to auth_method='SESSION' and keeps working exactly as it does today.

Revision ID: 0074_hr_integration_api_key
Revises: 0073_hr_integration
"""
from alembic import op
import sqlalchemy as sa

revision = "0074_hr_integration_api_key"
down_revision = "0073_hr_integration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("hr_integration_settings")}
    if "auth_method" not in columns:
        op.add_column("hr_integration_settings", sa.Column("auth_method", sa.String(20), nullable=False, server_default="SESSION"))
    if "api_key_encrypted" not in columns:
        op.add_column("hr_integration_settings", sa.Column("api_key_encrypted", sa.String(1000), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("hr_integration_settings")}
    if "api_key_encrypted" in columns:
        op.drop_column("hr_integration_settings", "api_key_encrypted")
    if "auth_method" in columns:
        op.drop_column("hr_integration_settings", "auth_method")
