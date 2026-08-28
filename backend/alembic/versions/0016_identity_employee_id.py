"""Add employee_id / source to users — Phase 10: one identity row per person instead of a separate CSV
bookkeeping row plus a separate real-account row. `employee_id` (nullable, unique when set) becomes the
provider-independent key onboarding uses to find an existing identity regardless of which connector it currently
lives under; `source` records where it came from (e.g. 'CSV_ONBOARDING' — NULL means directory sync / manual).

Revision ID: 0016_identity_employee_id
Revises: 0015_real_provisioning
"""
from alembic import op
import sqlalchemy as sa

revision = "0016_identity_employee_id"
down_revision = "0015_real_provisioning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "employee_id" not in existing:
        op.add_column("users", sa.Column("employee_id", sa.String(100), nullable=True))
    if "source" not in existing:
        op.add_column("users", sa.Column("source", sa.String(50), nullable=True))
    existing_constraints = {c["name"] for c in sa.inspect(op.get_bind()).get_unique_constraints("users")}
    if "uq_users_employee_id" not in existing_constraints:
        op.create_unique_constraint("uq_users_employee_id", "users", ["employee_id"])


def downgrade() -> None:
    existing_constraints = {c["name"] for c in sa.inspect(op.get_bind()).get_unique_constraints("users")}
    if "uq_users_employee_id" in existing_constraints:
        op.drop_constraint("uq_users_employee_id", "users", type_="unique")
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("users")}
    if "source" in existing:
        op.drop_column("users", "source")
    if "employee_id" in existing:
        op.drop_column("users", "employee_id")
