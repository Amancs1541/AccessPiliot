"""Access package owners — users who may manage a package (rename it / remove items) from their own portal, and
who are suggested as reviewer for Access Reviews scoped to that package. Additive: one new table.

Revision ID: 0048_access_package_owners
Revises: 0047_access_review_recurrence
"""
from alembic import op
import sqlalchemy as sa

revision = "0048_access_package_owners"
down_revision = "0047_access_review_recurrence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "access_package_owners" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "access_package_owners",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("package_id", sa.Uuid(), sa.ForeignKey("access_packages.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("assigned_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("package_id", "user_id", name="uq_access_package_owners_pkg_user"),
    )
    op.create_index("ix_access_package_owners_user", "access_package_owners", ["user_id"])


def downgrade() -> None:
    if "access_package_owners" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_index("ix_access_package_owners_user", table_name="access_package_owners")
        op.drop_table("access_package_owners")
