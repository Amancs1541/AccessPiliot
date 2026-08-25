"""Add fallback_unlock_hours to access_packages and fallback_unlock_at to access_assignments — lets a
package's approver flow require the fallback approver to WAIT until the primary approver's response
window has elapsed before they may act, instead of either approver being able to decide at any time.
Also folds who-can-request/approver/fallback setup into package creation itself (schema-only change,
no new columns needed for that part — PackageCreate now just accepts the same fields
PackageEligibilityUpdate already did).

Revision ID: 0010_package_approver_flow
Revises: 0009_fallback_approver
"""
from alembic import op
import sqlalchemy as sa

revision = "0010_package_approver_flow"
down_revision = "0009_fallback_approver"
branch_labels = None
depends_on = None


def upgrade() -> None:
    package_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("access_packages")}
    if "fallback_unlock_hours" not in package_columns:
        op.add_column("access_packages", sa.Column("fallback_unlock_hours", sa.Integer(), nullable=True))

    assignment_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("access_assignments")}
    if "fallback_unlock_at" not in assignment_columns:
        op.add_column("access_assignments", sa.Column("fallback_unlock_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    package_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("access_packages")}
    if "fallback_unlock_hours" in package_columns:
        op.drop_column("access_packages", "fallback_unlock_hours")

    assignment_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("access_assignments")}
    if "fallback_unlock_at" in assignment_columns:
        op.drop_column("access_assignments", "fallback_unlock_at")
