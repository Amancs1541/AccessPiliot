"""New hr_integration_settings table (singleton): configures an automated connection to an external HR
'source of truth' app for the Joiner/Mover side of JML. Stores where to connect (base_url) and how (username +
Fernet-encrypted password, sync interval) — no employee data lives here; a scheduled worker logs in with these
credentials, fetches the HR app's CSV export, and feeds it through the existing onboarding engine unchanged.

Revision ID: 0073_hr_integration
Revises: 0072_joiner_provisioning_delay
"""
from alembic import op
import sqlalchemy as sa

revision = "0073_hr_integration"
down_revision = "0072_joiner_provisioning_delay"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "hr_integration_settings" in inspector.get_table_names():
        return
    op.create_table(
        "hr_integration_settings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("base_url", sa.String(500), nullable=True),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("password_encrypted", sa.String(1000), nullable=True),
        sa.Column("sync_interval_minutes", sa.Integer(), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_status", sa.String(20), nullable=True),
        sa.Column("last_sync_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "hr_integration_settings" in inspector.get_table_names():
        op.drop_table("hr_integration_settings")
