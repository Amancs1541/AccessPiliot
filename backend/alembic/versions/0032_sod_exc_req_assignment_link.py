"""Add granted_assignment_id to sod_exception_requests — the exact AccessAssignment a granted request produced,
recorded once at grant time so services.sod._find_exception_granted_assignment/get_sod_exception_covering_assignment
can look it up directly instead of re-deriving it heuristically by matching (user, resource, created_at). A real
live bug proved the heuristic unsafe: an old, already-handled request's match could reach forward in time and grab
a much later, unrelated assignment for the same target once its own original assignment had already been revoked.

Revision ID: 0032_sod_exc_req_assign_link
Revises: 0031_security_tz
"""
from alembic import op
import sqlalchemy as sa

revision = "0032_sod_exc_req_assign_link"
down_revision = "0031_security_tz"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("sod_exception_requests")}
    if "granted_assignment_id" not in columns:
        op.add_column("sod_exception_requests", sa.Column("granted_assignment_id", sa.Uuid(), sa.ForeignKey("access_assignments.id"), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("sod_exception_requests")}
    if "granted_assignment_id" in columns:
        op.drop_column("sod_exception_requests", "granted_assignment_id")
