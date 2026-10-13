"""lifecycle_settings gains joiner_retry_interval_minutes (NULL = off, unchanged behavior — a FAILED joiner
target only ever retries on a manual click) and last_joiner_retry_sweep_at (internal bookkeeping). When set, a
new background sweep (app.services.joiner.sweep_failed_joiner_targets) automatically retries every still-FAILED
target on every SCHEDULED/PARTIAL joiner every N minutes, so a directory (AD DS or Entra) coming back online
after being unreachable gets picked up without an admin having to notice and click Retry. Entirely additive.

Revision ID: 0076_joiner_retry_sweep
Revises: 0075_hr_sync_trigger_token
"""
from alembic import op
import sqlalchemy as sa

revision = "0076_joiner_retry_sweep"
down_revision = "0075_hr_sync_trigger_token"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("lifecycle_settings")}
    if "joiner_retry_interval_minutes" not in columns:
        op.add_column("lifecycle_settings", sa.Column("joiner_retry_interval_minutes", sa.Integer(), nullable=True))
    if "last_joiner_retry_sweep_at" not in columns:
        op.add_column("lifecycle_settings", sa.Column("last_joiner_retry_sweep_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("lifecycle_settings")}
    if "last_joiner_retry_sweep_at" in columns:
        op.drop_column("lifecycle_settings", "last_joiner_retry_sweep_at")
    if "joiner_retry_interval_minutes" in columns:
        op.drop_column("lifecycle_settings", "joiner_retry_interval_minutes")
