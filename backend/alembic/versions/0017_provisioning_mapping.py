"""Add provisioning_domain / username_convention to identity_providers — Phase 11: lets an Admin pick a KNOWN
VERIFIED domain (fetched live from the connector) and a naming-convention template for new accounts this
connector provisions, instead of trusting an arbitrary email domain straight from a CSV row.

Revision ID: 0017_provisioning_mapping
Revises: 0016_identity_employee_id
"""
from alembic import op
import sqlalchemy as sa

revision = "0017_provisioning_mapping"
down_revision = "0016_identity_employee_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("identity_providers")}
    if "provisioning_domain" not in existing:
        op.add_column("identity_providers", sa.Column("provisioning_domain", sa.String(255), nullable=True))
    if "username_convention" not in existing:
        op.add_column("identity_providers", sa.Column("username_convention", sa.String(100), nullable=True))


def downgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("identity_providers")}
    if "username_convention" in existing:
        op.drop_column("identity_providers", "username_convention")
    if "provisioning_domain" in existing:
        op.drop_column("identity_providers", "provisioning_domain")
