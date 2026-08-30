"""Add bootstrap_credentials, portal_auth_configs, breakglass_accounts — Phase 12 step 1: the AccessPilot
PORTAL's own login/AuthN scaffolding, deliberately separate from identity_providers (the HR-sync/provisioning
connector). Purely additive, zero rows created by this migration — every existing deployment with env-var Entra
already configured is completely unaffected (portal_setup_is_needed() short-circuits to False for them).

Revision ID: 0018_portal_authn
Revises: 0017_provisioning_mapping
"""
from alembic import op
import sqlalchemy as sa

revision = "0018_portal_authn"
down_revision = "0017_provisioning_mapping"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "bootstrap_credentials" not in existing_tables:
        op.create_table(
            "bootstrap_credentials",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("username", sa.String(100), nullable=False),
            sa.Column("password_hash", sa.String(255), nullable=False),
            sa.Column("session_secret", sa.String(255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )

    if "portal_auth_configs" not in existing_tables:
        op.create_table(
            "portal_auth_configs",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("idp_type", sa.String(20), nullable=False),
            sa.Column("tenant_id", sa.String(200), nullable=True),
            sa.Column("client_id", sa.String(255), nullable=True),
            sa.Column("authority", sa.String(500), nullable=True),
            sa.Column("issuer", sa.String(500), nullable=True),
            sa.Column("audience", sa.String(500), nullable=True),
            sa.Column("scope", sa.String(500), nullable=True),
            sa.Column("redirect_uri", sa.String(500), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )

    if "breakglass_accounts" not in existing_tables:
        op.create_table(
            "breakglass_accounts",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("username", sa.String(100), nullable=False, unique=True),
            sa.Column("password_hash", sa.String(255), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table in ("breakglass_accounts", "portal_auth_configs", "bootstrap_credentials"):
        if table in existing_tables:
            op.drop_table(table)
