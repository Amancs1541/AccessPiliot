"""Add Non-Human Identity (NHI) governance: nhi_type/credential_expires_at on applications, plus new
application_owners and nhi_risk_exceptions tables — extends the existing user/group/role governance discipline
(ownership, risk acceptance) to Entra service principals and Okta service apps.

Revision ID: 0035_nhi_management
Revises: 0034_security_support_email
"""
from alembic import op
import sqlalchemy as sa

revision = "0035_nhi_management"
down_revision = "0034_security_support_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("applications")}
    if "nhi_type" not in columns:
        op.add_column("applications", sa.Column("nhi_type", sa.String(50), nullable=False, server_default="SERVICE_PRINCIPAL"))
    if "credential_expires_at" not in columns:
        op.add_column("applications", sa.Column("credential_expires_at", sa.DateTime(timezone=True), nullable=True))

    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "application_owners" not in existing_tables:
        op.create_table(
            "application_owners",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("application_id", sa.Uuid(), sa.ForeignKey("applications.id"), nullable=False),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("assigned_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("application_id", "user_id", name="uq_application_owners_app_user"),
        )
    if "nhi_risk_exceptions" not in existing_tables:
        op.create_table(
            "nhi_risk_exceptions",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("application_id", sa.Uuid(), sa.ForeignKey("applications.id"), nullable=False),
            sa.Column("risk_type", sa.String(50), nullable=False),
            sa.Column("justification", sa.Text(), nullable=False),
            sa.Column("approved_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_nhi_risk_exceptions_app_type", "nhi_risk_exceptions", ["application_id", "risk_type"])


def downgrade() -> None:
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "nhi_risk_exceptions" in existing_tables:
        op.drop_table("nhi_risk_exceptions")
    if "application_owners" in existing_tables:
        op.drop_table("application_owners")
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("applications")}
    if "credential_expires_at" in columns:
        op.drop_column("applications", "credential_expires_at")
    if "nhi_type" in columns:
        op.drop_column("applications", "nhi_type")
