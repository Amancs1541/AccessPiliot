"""lifecycle_settings gains secondary_account_retry_interval_minutes (NULL = off, unchanged behavior — a missing
secondary directory account for an HR/CSV-synced person only retries on their next sync) and
last_secondary_account_retry_sweep_at (internal bookkeeping). When set, a new background sweep
(app.services.provisioning.sweep_missing_secondary_accounts) rechecks every real, active person every N minutes
regardless of their next HR sync — the same guarantee joiner_retry_interval_minutes already gives Joiner,
extended to the HR/CSV-onboarding multi-directory provisioning path. Entirely additive.

Revision ID: 0077_secondary_retry_sweep
Revises: 0076_joiner_retry_sweep
"""
from alembic import op
import sqlalchemy as sa

revision = "0077_secondary_retry_sweep"
down_revision = "0076_joiner_retry_sweep"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("lifecycle_settings")}
    if "secondary_account_retry_interval_minutes" not in columns:
        op.add_column("lifecycle_settings", sa.Column("secondary_account_retry_interval_minutes", sa.Integer(), nullable=True))
    if "last_secondary_account_retry_sweep_at" not in columns:
        op.add_column("lifecycle_settings", sa.Column("last_secondary_account_retry_sweep_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("lifecycle_settings")}
    if "last_secondary_account_retry_sweep_at" in columns:
        op.drop_column("lifecycle_settings", "last_secondary_account_retry_sweep_at")
    if "secondary_account_retry_interval_minutes" in columns:
        op.drop_column("lifecycle_settings", "secondary_account_retry_interval_minutes")
