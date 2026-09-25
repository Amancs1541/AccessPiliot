"""Departments — a small Admin-managed lookup list (see app.models.models.Department /
app.services.departments) that populates the User Detail page's Department dropdown. Seeded with "AppDev" and
"IT" per the initial request; an Admin adds the rest from the Policies page.

Revision ID: 0043_departments
Revises: 0042_birthright_rule_engine
"""
from alembic import op
import sqlalchemy as sa

revision = "0043_departments"
down_revision = "0042_birthright_rule_engine"
branch_labels = None
depends_on = None

departments_table = sa.table("departments", sa.column("id", sa.Uuid()), sa.column("name", sa.String()), sa.column("created_at", sa.DateTime(timezone=True)))


def upgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "departments" not in existing_tables:
        op.create_table(
            "departments",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("name", sa.String(200), nullable=False, unique=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        import uuid
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        op.bulk_insert(departments_table, [
            {"id": uuid.uuid4(), "name": "AppDev", "created_at": now},
            {"id": uuid.uuid4(), "name": "IT", "created_at": now},
        ])


def downgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "departments" in existing_tables:
        op.drop_table("departments")
