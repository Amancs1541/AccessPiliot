from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from typing import Optional

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def uuid_pk() -> Mapped[UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid4)


def created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def updated_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class IdentityProvider(Base):
    __tablename__ = "identity_providers"
    id: Mapped[UUID] = uuid_pk(); name: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False); status: Mapped[str] = mapped_column(String(50), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(200), nullable=False); organization_url: Mapped[Optional[str]] = mapped_column(String(500))
    configuration_ref: Mapped[Optional[str]] = mapped_column(String(500)); client_id: Mapped[Optional[str]] = mapped_column(String(255)); authority: Mapped[Optional[str]] = mapped_column(String(500)); api_audience: Mapped[Optional[str]] = mapped_column(String(500)); api_scope: Mapped[Optional[str]] = mapped_column(String(500)); redirect_uri_metadata: Mapped[Optional[dict]] = mapped_column("redirect_uri_metadata", JSON); last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    graph_client_id: Mapped[Optional[str]] = mapped_column(String(255)); graph_client_secret_encrypted: Mapped[Optional[str]] = mapped_column(Text)
    sync_interval_minutes: Mapped[Optional[int]] = mapped_column(Integer)
    max_self_activation_hours: Mapped[int] = mapped_column(Integer, nullable=False, server_default="8")
    created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at()

    @property
    def credential_configured(self) -> bool:
        return bool(self.graph_client_secret_encrypted)


class User(Base):
    __tablename__ = "users"
    id: Mapped[UUID] = uuid_pk(); provider_id: Mapped[UUID] = mapped_column(ForeignKey("identity_providers.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False); email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False); given_name: Mapped[Optional[str]] = mapped_column(String(120)); surname: Mapped[Optional[str]] = mapped_column(String(120))
    department: Mapped[Optional[str]] = mapped_column(String(200)); job_title: Mapped[Optional[str]] = mapped_column(String(200)); status: Mapped[str] = mapped_column(String(50), nullable=False)
    employee_id: Mapped[Optional[str]] = mapped_column(String(100)); source: Mapped[Optional[str]] = mapped_column(String(50))
    created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at(); last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("provider_id", "external_id", name="uq_users_provider_external"), Index("ix_users_provider_external", "provider_id", "external_id"), UniqueConstraint("employee_id", name="uq_users_employee_id"))


class Group(Base):
    __tablename__ = "groups"
    id: Mapped[UUID] = uuid_pk(); provider_id: Mapped[UUID] = mapped_column(ForeignKey("identity_providers.id"), nullable=False); external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False); description: Mapped[Optional[str]] = mapped_column(Text); is_privileged: Mapped[bool] = mapped_column(nullable=False, default=False); status: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at(); last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("provider_id", "external_id", name="uq_groups_provider_external"), Index("ix_groups_provider_external", "provider_id", "external_id"))


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[UUID] = uuid_pk(); provider_id: Mapped[UUID] = mapped_column(ForeignKey("identity_providers.id"), nullable=False); external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False); description: Mapped[Optional[str]] = mapped_column(Text); role_type: Mapped[str] = mapped_column(String(50), nullable=False); is_privileged: Mapped[bool] = mapped_column(nullable=False, default=False); status: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at(); last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("provider_id", "external_id", name="uq_roles_provider_external"), Index("ix_roles_provider_external", "provider_id", "external_id"))


class Application(Base):
    __tablename__ = "applications"
    id: Mapped[UUID] = uuid_pk(); provider_id: Mapped[UUID] = mapped_column(ForeignKey("identity_providers.id"), nullable=False); external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False); status: Mapped[str] = mapped_column(String(50), nullable=False); app_roles: Mapped[Optional[list]] = mapped_column("app_roles", JSON)
    created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at(); last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("provider_id", "external_id", name="uq_applications_provider_external"), Index("ix_applications_provider_external", "provider_id", "external_id"))


class UserGroup(Base):
    __tablename__ = "user_groups"
    id: Mapped[UUID] = uuid_pk(); user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False); group_id: Mapped[UUID] = mapped_column(ForeignKey("groups.id"), nullable=False); source: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at(); __table_args__ = (UniqueConstraint("user_id", "group_id", name="uq_user_groups_membership"), Index("ix_user_groups_user_group", "user_id", "group_id"))


class RoleAssignment(Base):
    __tablename__ = "role_assignments"
    id: Mapped[UUID] = uuid_pk(); provider_id: Mapped[UUID] = mapped_column(ForeignKey("identity_providers.id"), nullable=False); user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False); role_id: Mapped[UUID] = mapped_column(ForeignKey("roles.id"), nullable=False); external_id: Mapped[str] = mapped_column(String(255), nullable=False); assignment_type: Mapped[str] = mapped_column(String(50), nullable=False); status: Mapped[str] = mapped_column(String(50), nullable=False); start_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True)); expiration_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True)); created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at(); last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True)); __table_args__ = (Index("ix_role_assignments_user", "user_id"), Index("ix_role_assignments_expiration", "expiration_time"))


class AccessAssignment(Base):
    __tablename__ = "access_assignments"
    id: Mapped[UUID] = uuid_pk(); provider_id: Mapped[UUID] = mapped_column(ForeignKey("identity_providers.id"), nullable=False); user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False); resource_type: Mapped[str] = mapped_column(String(50), nullable=False); resource_id: Mapped[UUID] = mapped_column(Uuid, nullable=False); app_role_external_id: Mapped[Optional[str]] = mapped_column(String(100)); assignment_type: Mapped[str] = mapped_column(String(50), nullable=False); status: Mapped[str] = mapped_column(String(50), nullable=False); start_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True)); expiration_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True)); justification: Mapped[Optional[str]] = mapped_column(Text); ticket_number: Mapped[Optional[str]] = mapped_column(String(100)); requested_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); approved_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); fallback_approver_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); fallback_unlock_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True)); bypass_activation: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false"); activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True)); revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True)); created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at(); __table_args__ = (Index("ix_access_assignments_user", "user_id"), Index("ix_access_assignments_status", "status"), Index("ix_access_assignments_expiration", "expiration_time"))


class AccessRequest(Base):
    __tablename__ = "access_requests"
    id: Mapped[UUID] = uuid_pk(); requester_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False); provider_id: Mapped[UUID] = mapped_column(ForeignKey("identity_providers.id"), nullable=False); resource_type: Mapped[str] = mapped_column(String(50), nullable=False); resource_id: Mapped[UUID] = mapped_column(Uuid, nullable=False); requested_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False); requested_start_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True)); requested_expiration_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True)); justification: Mapped[str] = mapped_column(Text, nullable=False); ticket_number: Mapped[Optional[str]] = mapped_column(String(100)); status: Mapped[str] = mapped_column(String(50), nullable=False); risk_level: Mapped[str] = mapped_column(String(50), nullable=False); created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at(); approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True)); rejected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True)); cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True)); __table_args__ = (Index("ix_access_requests_requester", "requester_id"), Index("ix_access_requests_status", "status"))


class ApprovalStep(Base):
    __tablename__ = "approval_steps"
    id: Mapped[UUID] = uuid_pk(); access_request_id: Mapped[UUID] = mapped_column(ForeignKey("access_requests.id"), nullable=False); step_number: Mapped[int] = mapped_column(Integer, nullable=False); approver_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False); status: Mapped[str] = mapped_column(String(50), nullable=False); comment: Mapped[Optional[str]] = mapped_column(Text); acted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True)); created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at()


class Policy(Base):
    __tablename__ = "policies"
    id: Mapped[UUID] = uuid_pk(); name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True); description: Mapped[Optional[str]] = mapped_column(Text); max_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False); require_mfa: Mapped[bool] = mapped_column(nullable=False, default=False); require_approval: Mapped[bool] = mapped_column(nullable=False, default=False); require_justification: Mapped[bool] = mapped_column(nullable=False, default=True); require_ticket: Mapped[bool] = mapped_column(nullable=False, default=False); risk_level: Mapped[str] = mapped_column(String(50), nullable=False); status: Mapped[str] = mapped_column(String(50), nullable=False); created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at()


class PolicyTarget(Base):
    __tablename__ = "policy_targets"
    id: Mapped[UUID] = uuid_pk(); policy_id: Mapped[UUID] = mapped_column(ForeignKey("policies.id"), nullable=False); provider_id: Mapped[UUID] = mapped_column(ForeignKey("identity_providers.id"), nullable=False); resource_type: Mapped[str] = mapped_column(String(50), nullable=False); resource_id: Mapped[UUID] = mapped_column(Uuid, nullable=False); created_at: Mapped[datetime] = created_at()


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[UUID] = uuid_pk(); timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False); actor_user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); action: Mapped[str] = mapped_column(String(100), nullable=False); target_type: Mapped[str] = mapped_column(String(100), nullable=False); target_id: Mapped[Optional[UUID]] = mapped_column(Uuid); provider_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("identity_providers.id")); request_id: Mapped[str] = mapped_column(String(100), nullable=False); result: Mapped[str] = mapped_column(String(50), nullable=False); ip_address: Mapped[Optional[str]] = mapped_column(String(64)); user_agent: Mapped[Optional[str]] = mapped_column(String(500)); metadata_json: Mapped[Optional[dict]] = mapped_column("metadata", JSON); created_at: Mapped[datetime] = created_at(); __table_args__ = (Index("ix_audit_logs_timestamp", "timestamp"), Index("ix_audit_logs_actor", "actor_user_id"), Index("ix_audit_logs_request", "request_id"))


class SyncRun(Base):
    __tablename__ = "sync_runs"
    id: Mapped[UUID] = uuid_pk(); provider_id: Mapped[UUID] = mapped_column(ForeignKey("identity_providers.id"), nullable=False); status: Mapped[str] = mapped_column(String(50), nullable=False); started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False); completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True)); users_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0); groups_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0); roles_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0); errors_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0); created_at: Mapped[datetime] = created_at(); __table_args__ = (Index("ix_sync_runs_provider", "provider_id"),)


class SyncError(Base):
    __tablename__ = "sync_errors"
    id: Mapped[UUID] = uuid_pk(); sync_run_id: Mapped[UUID] = mapped_column(ForeignKey("sync_runs.id"), nullable=False); resource_type: Mapped[str] = mapped_column(String(50), nullable=False); external_id: Mapped[str] = mapped_column(String(255), nullable=False); error_code: Mapped[str] = mapped_column(String(100), nullable=False); error_message: Mapped[str] = mapped_column(Text, nullable=False); metadata_json: Mapped[Optional[dict]] = mapped_column("metadata", JSON); created_at: Mapped[datetime] = created_at()


class ProviderResource(Base):
    __tablename__ = "provider_resources"
    id: Mapped[UUID] = uuid_pk(); provider_id: Mapped[UUID] = mapped_column(ForeignKey("identity_providers.id"), nullable=False); resource_type: Mapped[str] = mapped_column(String(50), nullable=False); external_id: Mapped[str] = mapped_column(String(255), nullable=False); display_name: Mapped[str] = mapped_column(String(255), nullable=False); metadata_json: Mapped[Optional[dict]] = mapped_column("metadata", JSON); created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at()


class AccessPackage(Base):
    __tablename__ = "access_packages"
    id: Mapped[UUID] = uuid_pk(); name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True); description: Mapped[Optional[str]] = mapped_column(Text); status: Mapped[str] = mapped_column(String(50), nullable=False); default_approver_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); default_fallback_approver_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); fallback_unlock_hours: Mapped[Optional[int]] = mapped_column(Integer); created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at()


class AccessPackageEligibility(Base):
    __tablename__ = "access_package_eligibility"
    id: Mapped[UUID] = uuid_pk(); package_id: Mapped[UUID] = mapped_column(ForeignKey("access_packages.id"), nullable=False); principal_type: Mapped[str] = mapped_column(String(20), nullable=False); principal_id: Mapped[UUID] = mapped_column(Uuid, nullable=False); created_at: Mapped[datetime] = created_at(); __table_args__ = (Index("ix_access_package_eligibility_package", "package_id"), UniqueConstraint("package_id", "principal_type", "principal_id", name="uq_package_eligibility_principal"))


class AccessPackageItem(Base):
    __tablename__ = "access_package_items"
    id: Mapped[UUID] = uuid_pk(); package_id: Mapped[UUID] = mapped_column(ForeignKey("access_packages.id"), nullable=False); resource_type: Mapped[str] = mapped_column(String(50), nullable=False); resource_id: Mapped[UUID] = mapped_column(Uuid, nullable=False); app_role_external_id: Mapped[Optional[str]] = mapped_column(String(100)); created_at: Mapped[datetime] = created_at(); __table_args__ = (Index("ix_access_package_items_package", "package_id"),)


class AccessPackageAssignment(Base):
    __tablename__ = "access_package_assignments"
    id: Mapped[UUID] = uuid_pk(); package_id: Mapped[UUID] = mapped_column(ForeignKey("access_packages.id"), nullable=False); package_assignment_id: Mapped[UUID] = mapped_column(Uuid, nullable=False); assignment_id: Mapped[UUID] = mapped_column(ForeignKey("access_assignments.id"), nullable=False, unique=True); user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False); created_at: Mapped[datetime] = created_at(); __table_args__ = (Index("ix_access_package_assignments_batch", "package_assignment_id"),)


class OnboardingImport(Base):
    __tablename__ = "onboarding_imports"
    id: Mapped[UUID] = uuid_pk(); provider_id: Mapped[UUID] = mapped_column(ForeignKey("identity_providers.id"), nullable=False); filename: Mapped[str] = mapped_column(String(255), nullable=False); status: Mapped[str] = mapped_column(String(50), nullable=False)
    total_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0); created_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0); updated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0); disabled_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0); no_change_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0); failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    access_revoked_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0"); access_revoke_failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    real_accounts_provisioned_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0"); birthright_assignments_created_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    uploaded_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); error_summary: Mapped[Optional[dict]] = mapped_column("error_summary", JSON)
    created_at: Mapped[datetime] = created_at(); completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_onboarding_imports_provider", "provider_id"),)


class BirthrightPolicy(Base):
    __tablename__ = "birthright_policies"
    id: Mapped[UUID] = uuid_pk(); name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True); match_field: Mapped[str] = mapped_column(String(50), nullable=False); match_value: Mapped[str] = mapped_column(String(255), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False); resource_id: Mapped[UUID] = mapped_column(Uuid, nullable=False); app_role_external_id: Mapped[Optional[str]] = mapped_column(String(100)); assignment_type: Mapped[str] = mapped_column(String(50), nullable=False, default="PERMANENT")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="ACTIVE"); created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at()
    __table_args__ = (Index("ix_birthright_policies_match", "match_field", "match_value"),)


class OnboardingImportRecord(Base):
    __tablename__ = "onboarding_import_records"
    id: Mapped[UUID] = uuid_pk(); import_id: Mapped[UUID] = mapped_column(ForeignKey("onboarding_imports.id"), nullable=False); row_number: Mapped[int] = mapped_column(Integer, nullable=False); employee_id: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False); error_message: Mapped[Optional[str]] = mapped_column(Text); raw_data: Mapped[Optional[dict]] = mapped_column("raw_data", JSON)
    created_at: Mapped[datetime] = created_at()
    __table_args__ = (Index("ix_onboarding_import_records_import", "import_id"),)
