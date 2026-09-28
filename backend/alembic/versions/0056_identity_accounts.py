"""JML foundation: identity_accounts (a person can hold an account in several IdPs; backfilled with one PRIMARY row per
existing non-CSV user) and users.start_date / leaver_date / employment_type. Additive.

Revision ID: 0056_identity_accounts
Revises: 0055_lifecycle_leaver_setting
"""
import uuid

from alembic import op
import sqlalchemy as sa

revision = "0056_identity_accounts"
down_revision = "0055_lifecycle_leaver_setting"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    if "start_date" not in user_columns:
        op.add_column("users", sa.Column("start_date", sa.Date(), nullable=True))
    if "leaver_date" not in user_columns:
        op.add_column("users", sa.Column("leaver_date", sa.Date(), nullable=True))
    if "employment_type" not in user_columns:
        op.add_column("users", sa.Column("employment_type", sa.String(20), nullable=True))
    if "identity_accounts" not in inspector.get_table_names():
        op.create_table(
            "identity_accounts",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("provider_id", sa.Uuid(), sa.ForeignKey("identity_providers.id"), nullable=False),
            sa.Column("external_id", sa.String(255), nullable=False),
            sa.Column("username", sa.String(320), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
            sa.Column("provisioned_by", sa.String(20), nullable=False, server_default="SYNC"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("provider_id", "external_id", name="uq_identity_accounts_provider_external"),
        )
        op.create_index("ix_identity_accounts_user", "identity_accounts", ["user_id"])
    table = sa.table("identity_accounts", sa.column("id", sa.Uuid()), sa.column("user_id", sa.Uuid()), sa.column("provider_id", sa.Uuid()), sa.column("external_id", sa.String()), sa.column("username", sa.String()), sa.column("status", sa.String()), sa.column("provisioned_by", sa.String()))
    have = {(row[0], row[1]) for row in bind.execute(sa.text("SELECT provider_id, external_id FROM identity_accounts")).fetchall()}
    rows = bind.execute(sa.text("SELECT u.id, u.provider_id, u.external_id, u.email, u.status FROM users u JOIN identity_providers p ON p.id = u.provider_id WHERE p.type <> 'CSV'")).fetchall()
    new_rows = [
        {"id": uuid.uuid4(), "user_id": row[0], "provider_id": row[1], "external_id": row[2], "username": row[3], "status": row[4] if row[4] in ("ACTIVE", "DISABLED") else "ACTIVE", "provisioned_by": "SYNC"}
        for row in rows if (row[1], row[2]) not in have
    ]
    if new_rows:
        op.bulk_insert(table, new_rows)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "identity_accounts" in inspector.get_table_names():
        op.drop_table("identity_accounts")
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    for name in ("employment_type", "leaver_date", "start_date"):
        if name in user_columns:
            op.drop_column("users", name)
