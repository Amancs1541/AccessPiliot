"""New hr_sync_trigger_tokens table: a narrowly-scoped Personal Access Token whose ONLY capability anywhere in
AccessPilot is authenticating to POST /hr-integration/sync-now as an alternate credential to a normal Entra-issued
admin bearer token — lets an external HR app's own UI trigger a real sync with a credential an admin explicitly
generated, fully audited (created_by) and revocable, never a bypass of the permission system (the existing admin
path through that same route is unchanged and always tried first). Entirely additive.

Revision ID: 0075_hr_sync_trigger_token
Revises: 0074_hr_integration_api_key
"""
from alembic import op
import sqlalchemy as sa

revision = "0075_hr_sync_trigger_token"
down_revision = "0074_hr_integration_api_key"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "hr_sync_trigger_tokens" in inspector.get_table_names():
        return
    op.create_table(
        "hr_sync_trigger_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "hr_sync_trigger_tokens" in inspector.get_table_names():
        op.drop_table("hr_sync_trigger_tokens")
