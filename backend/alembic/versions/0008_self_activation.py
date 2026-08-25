"""Add max_self_activation_hours to identity_providers — the Admin-configurable cap on how long an
end user may self-activate an eligible assignment for (Phase 5 eligible/activate model).

Revision ID: 0008_self_activation
Revises: 0007_package_eligibility
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_self_activation"
down_revision = "0007_package_eligibility"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("identity_providers")}
    if "max_self_activation_hours" not in existing:
        op.add_column("identity_providers", sa.Column("max_self_activation_hours", sa.Integer(), nullable=False, server_default="8"))


def downgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("identity_providers")}
    if "max_self_activation_hours" in existing:
        op.drop_column("identity_providers", "max_self_activation_hours")
