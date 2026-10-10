"""Active Directory connector, Phase 1 (agent connectivity only): identity_providers gains agent_api_key_hash
(PBKDF2-hashed, never the plaintext key) and agent_last_seen_at (heartbeat timestamp) — the on-prem AD sync
agent's own credential and liveness signal. No LDAP/AD sync logic yet; this phase only proves the agent can
reach AccessPilot over HTTPS. Entirely additive.

Revision ID: 0071_ad_agent_connectivity
Revises: 0070_entitlement_catalog
"""
from alembic import op
import sqlalchemy as sa

revision = "0071_ad_agent_connectivity"
down_revision = "0070_entitlement_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("identity_providers")}

    if "agent_api_key_hash" not in columns:
        op.add_column("identity_providers", sa.Column("agent_api_key_hash", sa.String(255), nullable=True))
    if "agent_last_seen_at" not in columns:
        op.add_column("identity_providers", sa.Column("agent_last_seen_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("identity_providers")}

    if "agent_last_seen_at" in columns:
        op.drop_column("identity_providers", "agent_last_seen_at")
    if "agent_api_key_hash" in columns:
        op.drop_column("identity_providers", "agent_api_key_hash")
