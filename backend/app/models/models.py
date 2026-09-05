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
    provisioning_domain: Mapped[Optional[str]] = mapped_column(String(255)); username_convention: Mapped[Optional[str]] = mapped_column(String(100))
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


class SodPolicy(Base):
    """A Separation-of-Duties rule: two named conflict sides (entities live in SodPolicyEntity) — holding
    anything from side A *and* anything from side B simultaneously is a violation. Deliberately NOT reusing the
    dormant Policy/PolicyTarget models (wrong shape — a single-resource duration/approval rule, not a conflict
    pair)."""
    __tablename__ = "sod_policies"
    id: Mapped[UUID] = uuid_pk(); name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True); description: Mapped[Optional[str]] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="MEDIUM", server_default="MEDIUM"); status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE", server_default="ACTIVE")
    created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at()


class SodPolicyEntity(Base):
    """One member of a SodPolicy's conflict side. entity_type PACKAGE is resolved live against
    AccessPackageItem at check-time (never duplicated), so editing a package's items automatically updates what
    the rule means with no migration needed."""
    __tablename__ = "sod_policy_entities"
    id: Mapped[UUID] = uuid_pk(); sod_policy_id: Mapped[UUID] = mapped_column(ForeignKey("sod_policies.id"), nullable=False); conflict_side: Mapped[str] = mapped_column(String(1), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False); entity_id: Mapped[UUID] = mapped_column(Uuid, nullable=False); app_role_external_id: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[datetime] = created_at()
    __table_args__ = (Index("ix_sod_policy_entities_policy", "sod_policy_id"), UniqueConstraint("sod_policy_id", "conflict_side", "entity_type", "entity_id", "app_role_external_id", name="uq_sod_policy_entity"))


class SodAdmin(Base):
    """RETIRED — no longer read or written anywhere in the app. Originally an in-app, non-Entra way to flag a
    directory user as AccessPilot.SoDAdmin (a regular Admin granted/revoked this from inside AccessPilot itself,
    folded into the caller's effective roles at request time in security/auth.py). Removed deliberately: it let
    a plain Admin grant themselves or anyone else SoD governance with zero Entra involvement, defeating the
    whole point of keeping SoD rule-editing separate from Admin — AccessPilot.SoDAdmin is now sourced exclusively
    from a real Entra App Role assignment, exactly like every other AccessPilot role. Table (and this model) kept
    only so a `Base.metadata.create_all` never needs a migration change here; not read by any service or API."""
    __tablename__ = "sod_admins"
    id: Mapped[UUID] = uuid_pk(); user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, unique=True); granted_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); created_at: Mapped[datetime] = created_at()


class SodException(Base):
    """A formally accepted, time-boxed risk: a specific (policy, user) pair for which the conflict is known and
    deliberately tolerated, rather than eliminated. Unlike everything else in the SoD engine, this genuinely
    needs to be stored, not live-computed — "we accepted this risk until <date>" is a real decision that must
    survive across scans, not something derivable from current access state. Scoped to (policy, user), not to
    the specific entitlements held at grant time — the point is "this user is cleared on this rule," not "this
    exact pair of resources is cleared," so it still applies if which entitlement satisfies each side changes
    later. SOD_MANAGE-gated (SoDAdmin only, same as editing the rule itself — an Admin granting exceptions would
    be an equally effective way to defeat the engine as an Admin editing rules directly, which is already
    forbidden)."""
    __tablename__ = "sod_exceptions"
    id: Mapped[UUID] = uuid_pk(); sod_policy_id: Mapped[UUID] = mapped_column(ForeignKey("sod_policies.id"), nullable=False); user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    justification: Mapped[str] = mapped_column(Text, nullable=False); granted_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False); revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    __table_args__ = (Index("ix_sod_exceptions_policy_user", "sod_policy_id", "user_id"),)


class SodNotificationSettings(Base):
    """Singleton row (same get-or-create-on-first-read pattern as SecuritySettings/BrandingSettings) controlling
    whether/when the SoD engine's reconciliation pass (see services/sod.py) creates a SodNotification row. Both
    toggles default True (notify by default, matching this app's "the feature should visibly work once built"
    convention) but the threshold itself is nullable-with-a-default so an Admin can tune it without a migration."""
    __tablename__ = "sod_notification_settings"
    id: Mapped[UUID] = uuid_pk()
    notify_on_new_violation: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    notify_on_exception_expiring: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    exception_expiring_warning_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7, server_default="7")
    # Unlike the other two toggles, this gates a notification created eagerly at the moment of the event (see
    # create_sod_exception_request) rather than one produced by the reconciliation pass — still worth its own
    # switch, since an SoDAdmin who doesn't want to be pinged on every request should be able to turn it off.
    notify_on_exception_requested: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    # Anti-gaming measure: without this, a user can dodge every check by deactivating one side and immediately
    # activating the other, since no single moment ever has both sides simultaneously ACTIVE. Defaults OFF
    # (server_default="false") — unlike the two notification toggles above, this is a real behavior change to
    # the preventive gate, not just a notification preference, so it stays off until an Admin deliberately opts
    # in, matching this app's "off by default" convention for every new enforcement lever added this session.
    cooldown_enabled: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    cooldown_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24, server_default="24")
    updated_at: Mapped[datetime] = updated_at()


class SodNotification(Base):
    """The notification log — genuinely stored, like SodException, since "we already told someone about this"
    must survive across reconciliation passes (otherwise the same violation would re-notify every time
    get_sod_violations() happens to run). Created/resolved by reconcile_sod_notifications() (services/sod.py),
    which runs opportunistically whenever violations or exceptions are read (no separate scheduler/background
    worker needed) rather than on a fixed poll interval — consistent with the rest of this engine's
    "live, on-read compute" philosophy. resolved_at is set once the underlying condition stops being true (the
    violation is no longer found, or the exception is revoked/no longer within the warning window) — this is
    what lets reconciliation avoid re-notifying for something already flagged and still ongoing."""
    __tablename__ = "sod_notifications"
    id: Mapped[UUID] = uuid_pk()
    notification_type: Mapped[str] = mapped_column(String(50), nullable=False)
    sod_policy_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("sod_policies.id"))
    user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id"))
    sod_exception_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("sod_exceptions.id"))
    sod_exception_request_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("sod_exception_requests.id"))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    __table_args__ = (Index("ix_sod_notifications_open", "notification_type", "sod_policy_id", "user_id", "resolved_at"),)


class SodExceptionRequest(Base):
    """Bridges a BLOCKED assignment attempt to the exception-grant workflow: an Admin who hits a SOD_CONFLICT
    while creating an assignment (see check_sod_at_creation on create_assignment) can request an exception
    instead of just being stuck, or using override_sod to push through unilaterally. Stores enough of the
    original attempt's shape (approver/fallback/duration below) that granting can recreate it faithfully via
    create_assignment() itself — including routing through the same approver if one was configured — rather than
    just clearing the way for the admin to redo it manually."""
    __tablename__ = "sod_exception_requests"
    id: Mapped[UUID] = uuid_pk()
    sod_policy_id: Mapped[UUID] = mapped_column(ForeignKey("sod_policies.id"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    requested_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id"))
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    app_role_external_id: Mapped[Optional[str]] = mapped_column(String(100))
    # The rest of the original blocked AssignmentCreate's shape — captured so a grant can recreate it exactly,
    # not just a bare no-approver ELIGIBLE row. All optional/defaulted since only the direct Assignments form
    # (not package assignment, which has no "Request SoD Exception" button yet) populates these today.
    approver_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id"))
    fallback_approver_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id"))
    fallback_unlock_hours: Mapped[Optional[int]] = mapped_column(Integer)
    assignment_type: Mapped[str] = mapped_column(String(50), nullable=False, default="PERMANENT", server_default="PERMANENT")
    expiration_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", server_default="PENDING")
    decided_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    denial_reason: Mapped[Optional[str]] = mapped_column(Text)
    sod_exception_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("sod_exceptions.id"))
    # Set once, at the moment this request is granted, to the exact AccessAssignment create_assignment() produced
    # (see grant_sod_exception_request). Exists so services.sod._find_exception_granted_assignment can look this
    # up directly instead of re-deriving it by matching (user, resource, created_at) — a heuristic that a real
    # live bug proved unsafe: an old, already-fully-handled request's heuristic match could reach forward in time
    # and grab a much later, wholly unrelated assignment for the same target once its own original assignment had
    # already been revoked, since the heuristic had a lower time bound but no upper one.
    granted_assignment_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("access_assignments.id"))
    created_at: Mapped[datetime] = created_at()


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


class BootstrapCredential(Base):
    """First-run-only local login for the AccessPilot portal itself — deliberately separate from `identity_providers`
    (the HR-sync/provisioning connector table). Exists only until a real portal login IDP is configured and
    validated; the row is deleted the instant setup completes, which also makes every setup-session token issued
    against its `session_secret` permanently unverifiable — no separate expiry/revocation bookkeeping needed."""
    __tablename__ = "bootstrap_credentials"
    id: Mapped[UUID] = uuid_pk(); username: Mapped[str] = mapped_column(String(100), nullable=False); password_hash: Mapped[str] = mapped_column(String(255), nullable=False); session_secret: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = created_at()


class PortalAuthConfig(Base):
    """The IDP used to log into the AccessPilot portal itself — separate from `identity_providers` (the HR-sync/
    provisioning connector), even when both happen to point at the same real tenant. Only one row should ever be
    `is_active`; a pending (inactive) row is used during setup while its real-login test is in flight."""
    __tablename__ = "portal_auth_configs"
    id: Mapped[UUID] = uuid_pk(); idp_type: Mapped[str] = mapped_column(String(20), nullable=False)
    tenant_id: Mapped[Optional[str]] = mapped_column(String(200)); client_id: Mapped[Optional[str]] = mapped_column(String(255)); authority: Mapped[Optional[str]] = mapped_column(String(500))
    issuer: Mapped[Optional[str]] = mapped_column(String(500)); audience: Mapped[Optional[str]] = mapped_column(String(500)); scope: Mapped[Optional[str]] = mapped_column(String(500)); redirect_uri: Mapped[Optional[str]] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at()


class BreakGlassAccount(Base):
    """Local username/password emergency recovery account for the AccessPilot portal — created during setup,
    dormant (`is_active=False`) until the real portal IDP is validated and setup completes. Not a normal login
    path: meant only for recovering portal access if the configured IDP itself later breaks."""
    __tablename__ = "breakglass_accounts"
    id: Mapped[UUID] = uuid_pk(); username: Mapped[str] = mapped_column(String(100), nullable=False, unique=True); password_hash: Mapped[str] = mapped_column(String(255), nullable=False); session_secret: Mapped[Optional[str]] = mapped_column(String(255))
    emergency_path_token: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = created_at(); last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class SecuritySettings(Base):
    """Singleton row (there is only ever one) governing idle-session behavior for every signed-in user, admin and
    end-user alike — blur the screen after `blur_after_minutes` of inactivity, and/or show a click-to-resume lock
    screen after `lock_after_minutes`, and/or actually sign the user out after `logout_after_minutes`. All three
    are independent on/off toggles with their own threshold; blur/lock never sign the user out — only the
    logout tier ends the session."""
    __tablename__ = "security_settings"
    id: Mapped[UUID] = uuid_pk()
    blur_enabled: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    blur_after_minutes: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")
    lock_enabled: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    lock_after_minutes: Mapped[int] = mapped_column(nullable=False, default=5, server_default="5")
    logout_enabled: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    logout_after_minutes: Mapped[int] = mapped_column(nullable=False, default=15, server_default="15")
    # The one tenant-wide display setting on this otherwise idle-behavior-only table — lives here because this
    # is already the one settings row every signed-in user (not just Admin) fetches on load (GET is open to any
    # authenticated user, see api/v1/security_settings.py), which every date/time display in the app needs to
    # be able to read regardless of role. An IANA zone identifier (e.g. "Europe/Berlin"), validated with
    # zoneinfo.ZoneInfo at save time — every displayed date/time in the app is shown in this zone, uniformly for
    # every viewer, rather than each browser's own local timezone.
    timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="Europe/Berlin", server_default="Europe/Berlin")
    updated_at: Mapped[datetime] = updated_at()


class BrandingSettings(Base):
    """Singleton row governing white-label branding: the big centered logo on the public sign-in screen, the
    small sidebar logo inside the authenticated app, and the "Powered by" attribution text shown on both. Logos
    are stored as base64 data URIs directly in the row (this app's established convention for small binary
    uploads without adding python-multipart as a dependency — see the CSV onboarding upload). NULL fields mean
    "use the bundled default" — every deployment renders identically to before this feature existed until an
    Admin deliberately uploads something via the Branding settings page."""
    __tablename__ = "branding_settings"
    id: Mapped[UUID] = uuid_pk()
    sign_in_logo: Mapped[Optional[str]] = mapped_column(Text)
    internal_logo: Mapped[Optional[str]] = mapped_column(Text)
    powered_by_text: Mapped[Optional[str]] = mapped_column(String(100))
    updated_at: Mapped[datetime] = updated_at()


class Notification(Base):
    """General-purpose, per-user notification — deliberately a SEPARATE table/model from SodNotification, not a
    generalization of it. SodNotification's read state is a single global flag shared by every Admin/SoDAdmin,
    which is correct at that small-team scale but wrong here: every ordinary end user needs their own unread
    count, so read_at belongs to this row (one row per recipient) rather than to a shared condition. Unlike
    SodNotification's reconcile-a-currently-true-condition model, this one is pure discrete-event logging —
    something happened once (an assignment was created, approved, rejected...), so there is no resolved_at /
    auto-resolution concept here; a row is created once and only ever transitions unread -> read."""
    __tablename__ = "notifications"
    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    notification_type: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    link: Mapped[Optional[str]] = mapped_column(String(255))
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
