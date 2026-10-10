"""Global, admin-configurable Joiner provisioning delay: lifecycle_settings gains
joiner_provisioning_delay_days (NULL = unchanged behavior, accounts created immediately on submission). A delayed
joiner is held in a new PENDING_PROVISIONING status, so joiner_requests gains provision_at (when to actually
create the accounts) plus requested_targets/profile_extra (the resolved target list and optional profile fields
from the original submission, persisted so a deferred joiner can be provisioned later with exactly what was
requested). Entirely additive.

Revision ID: 0072_joiner_provisioning_delay
Revises: 0071_ad_agent_connectivity
"""
from alembic import op
import sqlalchemy as sa

revision = "0072_joiner_provisioning_delay"
down_revision = "0071_ad_agent_connectivity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    lifecycle_columns = {c["name"] for c in inspector.get_columns("lifecycle_settings")}
    if "joiner_provisioning_delay_days" not in lifecycle_columns:
        op.add_column("lifecycle_settings", sa.Column("joiner_provisioning_delay_days", sa.Integer(), nullable=True))

    joiner_columns = {c["name"] for c in inspector.get_columns("joiner_requests")}
    if "provision_at" not in joiner_columns:
        op.add_column("joiner_requests", sa.Column("provision_at", sa.DateTime(timezone=True), nullable=True))
    if "requested_targets" not in joiner_columns:
        op.add_column("joiner_requests", sa.Column("requested_targets", sa.JSON(), nullable=True))
    if "profile_extra" not in joiner_columns:
        op.add_column("joiner_requests", sa.Column("profile_extra", sa.JSON(), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    joiner_columns = {c["name"] for c in inspector.get_columns("joiner_requests")}
    if "profile_extra" in joiner_columns:
        op.drop_column("joiner_requests", "profile_extra")
    if "requested_targets" in joiner_columns:
        op.drop_column("joiner_requests", "requested_targets")
    if "provision_at" in joiner_columns:
        op.drop_column("joiner_requests", "provision_at")

    lifecycle_columns = {c["name"] for c in inspector.get_columns("lifecycle_settings")}
    if "joiner_provisioning_delay_days" in lifecycle_columns:
        op.drop_column("lifecycle_settings", "joiner_provisioning_delay_days")
