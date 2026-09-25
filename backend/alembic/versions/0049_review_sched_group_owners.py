"""Access Review fixed-calendar recurrence (schedule_* + next_run_at on campaigns) and AccessPilot-side group
owners (new group_owners table). Fully additive.

Revision ID: 0049_review_sched_group_owners
Revises: 0048_access_package_owners
"""
from alembic import op
import sqlalchemy as sa

revision = "0049_review_sched_group_owners"
down_revision = "0048_access_package_owners"
branch_labels = None
depends_on = None

COLUMNS = [
    ("schedule_day_of_month", sa.Integer()),
    ("schedule_time", sa.String(5)),
    ("schedule_every_months", sa.Integer()),
    ("schedule_due_days", sa.Integer()),
    ("next_run_at", sa.DateTime(timezone=True)),
]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("access_review_campaigns")}
    for name, column_type in COLUMNS:
        if name not in existing:
            op.add_column("access_review_campaigns", sa.Column(name, column_type, nullable=True))
    if "group_owners" not in inspector.get_table_names():
        op.create_table(
            "group_owners",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("group_id", sa.Uuid(), sa.ForeignKey("groups.id"), nullable=False),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("assigned_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("group_id", "user_id", name="uq_group_owners_group_user"),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "group_owners" in inspector.get_table_names():
        op.drop_table("group_owners")
    existing = {column["name"] for column in inspector.get_columns("access_review_campaigns")}
    for name, _ in COLUMNS:
        if name in existing:
            op.drop_column("access_review_campaigns", name)
