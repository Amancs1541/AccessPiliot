from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from typing import Optional

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid, func
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
    # Joiner process: is this IdP a default target when a new joiner's accounts are created? (per-IdP switch, default on)
    provision_joiners: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
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
    # Privileged (PU) / Test (TU) shadow accounts — see app.services.privileged_accounts. NORMAL (the default)
    # for every real, human-owned identity. linked_user_id is who a PU/TU account really belongs to — NULL for
    # a NORMAL account, and deliberately NULL-able for a PU/TU account too (an Admin-created one starts
    # unassociated on purpose; see the association gate in assignments.create_assignment).
    account_type: Mapped[str] = mapped_column(String(20), nullable=False, default="NORMAL", server_default="NORMAL")
    linked_user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id"))
    # Org-hierarchy tagging (see app.services.user_hierarchy) — purely AccessPilot-internal, never synced to/from
    # Entra's own native `manager` relationship. NULL (unclassified) for every existing user until an Admin tags
    # it — no forced backfill, same convention as account_type defaulting NORMAL for pre-existing rows.
    # manager_id is deliberately NOT restricted to EMPLOYEE-tagged rows: a MANAGER can have their own manager_id
    # too, which is what makes the Org Chart a real multi-level tree instead of a flat two-tier list. A separate
    # column from linked_user_id on purpose — different relationship, different lifecycle.
    employee_category: Mapped[Optional[str]] = mapped_column(String(20))
    manager_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id"))
    # Joiner/leaver lifecycle dates and the kind of worker — used by the joiner form and by leaver policies (scoped by
    # department / employment_type). Local to AccessPilot: nothing here is pushed to the directory.
    start_date: Mapped[Optional[date]] = mapped_column(Date)
    leaver_date: Mapped[Optional[date]] = mapped_column(Date)
    employment_type: Mapped[Optional[str]] = mapped_column(String(20))
    # Set once the leaver process has run for this person (scheduled, CSV, sync or manual); cleared when the leaver
    # date is changed. leaver_reminders_sent lists the 'N days before' reminders already sent for the current date.
    leaver_processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    leaver_reminders_sent: Mapped[Optional[list]] = mapped_column("leaver_reminders_sent", JSON)
    # Set by the leaver process from the person's leaver policy (delete_after_days); when the moment passes the worker
    # deletes their account in every IdP and stamps accounts_deleted_at (status becomes DELETED). The row itself stays
    # so history and audit remain intact.
    accounts_delete_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    accounts_deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at(); last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("provider_id", "external_id", name="uq_users_provider_external"), Index("ix_users_provider_external", "provider_id", "external_id"), UniqueConstraint("employee_id", name="uq_users_employee_id"))


class Group(Base):
    __tablename__ = "groups"
    id: Mapped[UUID] = uuid_pk(); provider_id: Mapped[UUID] = mapped_column(ForeignKey("identity_providers.id"), nullable=False); external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False); description: Mapped[Optional[str]] = mapped_column(Text); is_privileged: Mapped[bool] = mapped_column(nullable=False, default=False); status: Mapped[str] = mapped_column(String(50), nullable=False)
    # Admin-set, cosmetic reference fields — same pattern as BirthrightPolicy.external_policy_id: never looked up
    # internally (the real UUID id is what every FK uses), purely so a resource can be cited by a short business
    # code (e.g. in a ticket, a Business Role mapping row, or an audit note) and documented against the naming
    # pattern it's supposed to follow. Directory sync's upsert_group() never writes either field, so a value here
    # survives every re-sync untouched with no extra override-flag needed.
    resource_code: Mapped[Optional[str]] = mapped_column(String(100), unique=True); naming_convention: Mapped[Optional[str]] = mapped_column(String(255))
    # Admin-picked at creation time only (STANDARD/PRIVILEGED built-in, or a custom name from GroupLabel) — same
    # "directory sync never writes this" guarantee as resource_code/naming_convention above, confirmed by
    # upsert_group() only ever assigning name/description/is_privileged/status/last_synced_at.
    group_label: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at(); last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("provider_id", "external_id", name="uq_groups_provider_external"), Index("ix_groups_provider_external", "provider_id", "external_id"))


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[UUID] = uuid_pk(); provider_id: Mapped[UUID] = mapped_column(ForeignKey("identity_providers.id"), nullable=False); external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False); description: Mapped[Optional[str]] = mapped_column(Text); role_type: Mapped[str] = mapped_column(String(50), nullable=False); is_privileged: Mapped[bool] = mapped_column(nullable=False, default=False); status: Mapped[str] = mapped_column(String(50), nullable=False)
    # See Group.resource_code/naming_convention — identical cosmetic, sync-safe reference fields.
    resource_code: Mapped[Optional[str]] = mapped_column(String(100), unique=True); naming_convention: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at(); last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("provider_id", "external_id", name="uq_roles_provider_external"), Index("ix_roles_provider_external", "provider_id", "external_id"))


class Application(Base):
    __tablename__ = "applications"
    id: Mapped[UUID] = uuid_pk(); provider_id: Mapped[UUID] = mapped_column(ForeignKey("identity_providers.id"), nullable=False); external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False); status: Mapped[str] = mapped_column(String(50), nullable=False); app_roles: Mapped[Optional[list]] = mapped_column("app_roles", JSON)
    # Non-Human Identity governance fields (see app.services.nhi) — additive to the existing app-role-assignment
    # use of this table, not a replacement for it.
    nhi_type: Mapped[str] = mapped_column(String(50), nullable=False, default="SERVICE_PRINCIPAL", server_default="SERVICE_PRINCIPAL")
    # True once an NHIAdmin has manually reclassified nhi_type (e.g. tagging a generic service principal as an
    # AI agent, bot, or API) — sync then leaves it alone instead of stomping the manual call back to whatever the
    # provider itself reports on the next run. False (the default) means nhi_type still just reflects whatever
    # the connector auto-detected at last sync.
    nhi_type_overridden: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    credential_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Full secret/certificate list behind credential_expires_at (which is just the soonest of these) — powers the
    # NHI detail page's "Certificates & Secrets" section. List of {credential_type, display_name, expires_at}.
    nhi_credentials: Mapped[Optional[list]] = mapped_column(JSON)
    # See Group.resource_code/naming_convention — identical cosmetic, sync-safe reference fields.
    resource_code: Mapped[Optional[str]] = mapped_column(String(100), unique=True); naming_convention: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at(); last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("provider_id", "external_id", name="uq_applications_provider_external"), Index("ix_applications_provider_external", "provider_id", "external_id"))


class ApplicationOwner(Base):
    """A human user accountable for a non-human identity (an Application row — an Entra service principal or
    Okta service app). Purely an AccessPilot-internal accountability record, independent of whatever "owner"
    concept (if any) the source provider itself tracks — this is who AccessPilot asks when this NHI's access or
    credentials need a decision. A many-to-many join (not one owner column on Application) since real apps are
    often co-owned."""
    __tablename__ = "application_owners"
    id: Mapped[UUID] = uuid_pk(); application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id"), nullable=False); user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    assigned_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); created_at: Mapped[datetime] = created_at()
    __table_args__ = (UniqueConstraint("application_id", "user_id", name="uq_application_owners_app_user"),)


class NhiRiskException(Base):
    """A formally accepted, time-boxed NHI risk (e.g. "no owner assigned," "credential not rotated in a year") —
    same reasoning as SodException: risk-acceptance is a real decision that must survive across scans, unlike
    the risk finding itself, which is always live-computed (see app.services.nhi)."""
    __tablename__ = "nhi_risk_exceptions"
    id: Mapped[UUID] = uuid_pk(); application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id"), nullable=False); risk_type: Mapped[str] = mapped_column(String(50), nullable=False)
    justification: Mapped[str] = mapped_column(Text, nullable=False); approved_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False); revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    __table_args__ = (Index("ix_nhi_risk_exceptions_app_type", "application_id", "risk_type"),)


class UserGroup(Base):
    __tablename__ = "user_groups"
    id: Mapped[UUID] = uuid_pk(); user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False); group_id: Mapped[UUID] = mapped_column(ForeignKey("groups.id"), nullable=False); source: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at(); __table_args__ = (UniqueConstraint("user_id", "group_id", name="uq_user_groups_membership"), Index("ix_user_groups_user_group", "user_id", "group_id"))


class RoleAssignment(Base):
    __tablename__ = "role_assignments"
    id: Mapped[UUID] = uuid_pk(); provider_id: Mapped[UUID] = mapped_column(ForeignKey("identity_providers.id"), nullable=False); user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False); role_id: Mapped[UUID] = mapped_column(ForeignKey("roles.id"), nullable=False); external_id: Mapped[str] = mapped_column(String(255), nullable=False); assignment_type: Mapped[str] = mapped_column(String(50), nullable=False); status: Mapped[str] = mapped_column(String(50), nullable=False); start_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True)); expiration_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True)); created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at(); last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True)); __table_args__ = (Index("ix_role_assignments_user", "user_id"), Index("ix_role_assignments_expiration", "expiration_time"))


class AccessAssignment(Base):
    __tablename__ = "access_assignments"
    id: Mapped[UUID] = uuid_pk(); provider_id: Mapped[UUID] = mapped_column(ForeignKey("identity_providers.id"), nullable=False); user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False); resource_type: Mapped[str] = mapped_column(String(50), nullable=False); resource_id: Mapped[UUID] = mapped_column(Uuid, nullable=False); app_role_external_id: Mapped[Optional[str]] = mapped_column(String(100)); assignment_type: Mapped[str] = mapped_column(String(50), nullable=False); status: Mapped[str] = mapped_column(String(50), nullable=False); start_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True)); expiration_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True)); justification: Mapped[Optional[str]] = mapped_column(Text); ticket_number: Mapped[Optional[str]] = mapped_column(String(100)); requested_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); approved_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); fallback_approver_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); fallback_unlock_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True)); bypass_activation: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    # Set only when this assignment was created BY birthright-policy evaluation (see app.services.birthright) —
    # NULL for every manually/admin-granted or self-requested assignment. This is the safety rail that lets
    # reconcile_birthright_policies_for_user() know it's safe to auto-revoke this specific grant if the policy
    # stops matching the user later, while NEVER touching anything a human granted directly.
    birthright_policy_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("birthright_policies.id"))
    # Same idea as birthright_policy_id, for a grant made by a GroupRoleMapping instead — set only when
    # create_assignment() was called by group-role-mapping evaluation, never a manual grant.
    group_role_mapping_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("group_role_mappings.id"))
    # Same provenance-tag idea, for a grant made by assigning a Business Role — set only when create_assignment()
    # was called from services.business_roles.assign_business_role, never a manual grant. role_assignment_id is a
    # plain batch id (not a FK), grouping every item one role-assignment action created — the same idea as
    # AccessPackageAssignment.package_assignment_id, but kept as a column here instead of a second join table.
    business_role_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("business_roles.id"))
    role_assignment_id: Mapped[Optional[UUID]] = mapped_column(Uuid)
    # Same provenance-tag idea again: set only when this assignment's approval was routed through the Workflow
    # Engine (see app.services.workflows) instead of a single approver/fallback — approved_by/fallback_approver_id
    # stay NULL in that case, since there is no single approver, the workflow's own stages are the approval.
    workflow_instance_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("workflow_instances.id"))
    activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True)); revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True)); created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at(); __table_args__ = (Index("ix_access_assignments_user", "user_id"), Index("ix_access_assignments_status", "status"), Index("ix_access_assignments_expiration", "expiration_time"), Index("ix_access_assignments_role_assignment", "role_assignment_id"))


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
    id: Mapped[UUID] = uuid_pk(); name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True); description: Mapped[Optional[str]] = mapped_column(Text); status: Mapped[str] = mapped_column(String(50), nullable=False); default_approver_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); default_fallback_approver_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); fallback_unlock_hours: Mapped[Optional[int]] = mapped_column(Integer)
    # The default approval method for a self-service request (POST /packages/{id}/request) — mutually exclusive
    # with default_approver_id/default_fallback_approver_id (see PackageCreate/PackageEligibilityUpdate's
    # validator). An admin's own direct /assign call is unaffected: it picks its own approver/workflow per call.
    workflow_definition_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("workflow_definitions.id"))
    created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at()


class LifecycleEvent(Base):
    """One detected Joiner/Mover/Leaver change for a person, recorded by app.services.lifecycle no matter which
    source noticed it (directory sync, an in-app attribute edit, or a CSV import) — the single record the Movers
    report and notifications are built from. `changes` is {field: {"from": ..., "to": ...}}; review_note says why
    no review campaign was started when review_campaign_id is NULL."""
    __tablename__ = "lifecycle_events"
    id: Mapped[UUID] = uuid_pk(); user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(20), nullable=False); source: Mapped[str] = mapped_column(String(20), nullable=False)
    changes: Mapped[Optional[dict]] = mapped_column("changes", JSON)
    revoked_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0"); granted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    review_campaign_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("access_review_campaigns.id")); review_note: Mapped[Optional[str]] = mapped_column(String(50))
    notified_user_ids: Mapped[Optional[list]] = mapped_column("notified_user_ids", JSON)
    # How many items in the started review belong to the mover's linked Privileged (PU) / Test (TU) accounts.
    privileged_flagged_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = created_at()
    __table_args__ = (Index("ix_lifecycle_events_user", "user_id"), Index("ix_lifecycle_events_type_created", "event_type", "created_at"))


class PendingMove(Base):
    """A department/job-title change that takes effect at a FUTURE moment (effective dating). Nothing about the
    person changes until effective_at: a worker then applies the whole change together — the attribute update
    (pushed to the directory provider like an in-app edit), the birthright reconcile, and the leftover-access
    review. status: SCHEDULED -> APPLIED | CANCELLED | FAILED."""
    __tablename__ = "pending_moves"
    id: Mapped[UUID] = uuid_pk(); user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    new_department: Mapped[Optional[str]] = mapped_column(String(255)); new_job_title: Mapped[Optional[str]] = mapped_column(String(255))
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="SCHEDULED", server_default="SCHEDULED")
    created_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); import_id: Mapped[Optional[UUID]] = mapped_column(Uuid)
    applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True)); failure_reason: Mapped[Optional[str]] = mapped_column(String(500))
    lifecycle_event_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("lifecycle_events.id"))
    created_at: Mapped[datetime] = created_at()
    __table_args__ = (Index("ix_pending_moves_status_effective", "status", "effective_at"), Index("ix_pending_moves_user", "user_id"))


class LifecycleSettings(Base):
    """Singleton (get-or-create, like SecuritySettings). lifecycle_owner_ids: users who act as reviewer/fallback
    (and, later, get notified) when a mover has no manager tagged in the Org Chart."""
    __tablename__ = "lifecycle_settings"
    id: Mapped[UUID] = uuid_pk()
    mover_review_enabled: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    review_due_days: Mapped[int] = mapped_column(Integer, nullable=False, default=14, server_default="14")
    # Leaver gap: when directory sync sees a person flip ACTIVE -> DISABLED in Entra/Okta, revoke ALL their access
    # (and disable their linked PU/TU accounts), exactly like the CSV leaver path. Off = only record the event.
    revoke_on_directory_disable: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    lifecycle_owner_ids: Mapped[Optional[list]] = mapped_column("lifecycle_owner_ids", JSON)
    updated_at: Mapped[datetime] = updated_at()


class JoinerRequest(Base):
    """A joiner submitted through the New joiner form: the person's details, when they start, and — per connected
    IdP — the account that was created (disabled) for them. `targets` is a list of {provider_id, provider_name,
    username, external_id, account_id, status (CREATED / ENABLED / FAILED), error}; temporary passwords are NEVER
    stored, they are shown once when the accounts are created. status: SCHEDULED (accounts exist, disabled, waiting
    for start_at) -> ACTIVE (all enabled) | PARTIAL (some accounts could not be created/enabled) | CANCELLED."""
    __tablename__ = "joiner_requests"
    id: Mapped[UUID] = uuid_pk(); user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id"))
    first_name: Mapped[str] = mapped_column(String(120), nullable=False); last_name: Mapped[str] = mapped_column(String(120), nullable=False)
    work_email: Mapped[str] = mapped_column(String(320), nullable=False); employee_id: Mapped[Optional[str]] = mapped_column(String(100))
    department: Mapped[Optional[str]] = mapped_column(String(200)); job_title: Mapped[Optional[str]] = mapped_column(String(200))
    manager_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); employee_category: Mapped[Optional[str]] = mapped_column(String(20)); employment_type: Mapped[Optional[str]] = mapped_column(String(20))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False); leaver_date: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="SCHEDULED", server_default="SCHEDULED")
    targets: Mapped[Optional[list]] = mapped_column("targets", JSON)
    created_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); created_at: Mapped[datetime] = created_at(); activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_joiner_requests_status_start", "status", "start_at"),)


class LeaverPolicy(Base):
    """What happens when someone leaves, and when. The first ACTIVE policy (lowest priority number) whose scope matches
    the person wins; the seeded Default (is_default, scope ALL, priority 1000) covers everyone else. effective_time is
    the time of day on the leaver date, in the app timezone, at which the leaver process runs; notify_days_before are
    reminders to the manager / lifecycle owners. The switches choose which leaver actions run (app.services.lifecycle.run_leaver)."""
    __tablename__ = "leaver_policies"
    id: Mapped[UUID] = uuid_pk(); name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100, server_default="100")
    scope_type: Mapped[str] = mapped_column(String(20), nullable=False, default="ALL", server_default="ALL"); scope_value: Mapped[Optional[str]] = mapped_column(String(200))
    effective_time: Mapped[str] = mapped_column(String(5), nullable=False, default="23:59", server_default="23:59")
    notify_days_before: Mapped[Optional[list]] = mapped_column("notify_days_before", JSON)
    revoke_access: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    disable_accounts: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    disable_privileged_accounts: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    remove_group_memberships: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    # NULL = never delete. Otherwise the person's accounts are deleted from every IdP this many days after the leaver
    # process ran (irreversible in the directory; Entra keeps a deleted user recoverable for 30 days).
    delete_after_days: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE", server_default="ACTIVE")
    is_default: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at()


class ReenableRequest(Base):
    """After the leaver process has run, enabling the person's accounts again needs a written reason and an approval
    from their manager (lifecycle owners when there is no manager). status: PENDING -> APPROVED | REJECTED | CANCELLED.
    approver_ids records who could decide at request time."""
    __tablename__ = "reenable_requests"
    id: Mapped[UUID] = uuid_pk(); user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    requested_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", server_default="PENDING")
    approver_ids: Mapped[Optional[list]] = mapped_column("approver_ids", JSON)
    # NULL = every disabled account (an "Enable in all IdPs" click); a list of IdentityAccount ids = only those
    # (a single-account "Enable" click) — approval enables exactly the accounts this request named, nothing more.
    account_ids: Mapped[Optional[list]] = mapped_column("account_ids", JSON)
    decided_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); decision_note: Mapped[Optional[str]] = mapped_column(String(1000))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True)); created_at: Mapped[datetime] = created_at()
    __table_args__ = (Index("ix_reenable_requests_user_status", "user_id", "status"),)


class LeaverRequest(Base):
    """A MANUAL 'Start leaver process now'. The initiator gives a justification, the person's accounts are disabled
    immediately (disabled_accounts remembers which ones were active so a denial can restore exactly those), then the
    manager (lifecycle owners when none) approves or denies. Approved -> the leaver process runs; denied -> accounts
    are enabled again and the initiator is notified. status: PENDING -> APPROVED | DENIED."""
    __tablename__ = "leaver_requests"
    id: Mapped[UUID] = uuid_pk(); user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    requested_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); justification: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", server_default="PENDING")
    approver_ids: Mapped[Optional[list]] = mapped_column("approver_ids", JSON); disabled_accounts: Mapped[Optional[list]] = mapped_column("disabled_accounts", JSON)
    decided_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); decision_note: Mapped[Optional[str]] = mapped_column(String(1000))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True)); outcome: Mapped[Optional[str]] = mapped_column(String(500))
    created_at: Mapped[datetime] = created_at()
    __table_args__ = (Index("ix_leaver_requests_user_status", "user_id", "status"),)


class UserAttributeChangeRequest(Base):
    """A department/job_title edit submitted on the User Detail page with a Workflow attached, instead of applying
    instantly. Nothing about the real IdP record or the local User row changes until the workflow instance
    resolves — see services.identity_attributes.apply_user_attribute_change (the same real-write + mover
    reconciliation sequence the instant edit path already ran, just deferred until approval) and
    services.workflows's USER_ATTRIBUTES completion branch. status: PENDING -> APPLIED | REJECTED."""
    __tablename__ = "user_attribute_change_requests"
    id: Mapped[UUID] = uuid_pk(); user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    previous_department: Mapped[Optional[str]] = mapped_column(String(200)); previous_job_title: Mapped[Optional[str]] = mapped_column(String(200))
    requested_department: Mapped[Optional[str]] = mapped_column(String(200)); requested_job_title: Mapped[Optional[str]] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", server_default="PENDING")
    workflow_instance_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("workflow_instances.id"))
    created_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); created_at: Mapped[datetime] = created_at()
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_user_attribute_change_requests_user_status", "user_id", "status"),)


class IdentityAccount(Base):
    """One directory account belonging to a person. A person (a `User` row) may hold an account in several connected
    IdPs; the person's own row keeps its (provider_id, external_id) and is mirrored here as the PRIMARY account
    (lazily, by app.services.accounts), extra IdP accounts only ever appear through this table. status is what
    AccessPilot last set/observed (ACTIVE / DISABLED); provisioned_by says how it came to exist (SYNC / JOINER / MANUAL)."""
    __tablename__ = "identity_accounts"
    id: Mapped[UUID] = uuid_pk(); user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    provider_id: Mapped[UUID] = mapped_column(ForeignKey("identity_providers.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False); username: Mapped[Optional[str]] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE", server_default="ACTIVE")
    provisioned_by: Mapped[str] = mapped_column(String(20), nullable=False, default="SYNC", server_default="SYNC")
    created_at: Mapped[datetime] = created_at()
    __table_args__ = (UniqueConstraint("provider_id", "external_id", name="uq_identity_accounts_provider_external"), Index("ix_identity_accounts_user", "user_id"))


class GroupOwner(Base):
    """An AccessPilot-side owner of a directory group — purely an internal accountability record (nothing is
    written to Entra), used e.g. to suggest the reviewer of an Access Review scoped to that group. Mirrors
    ApplicationOwner."""
    __tablename__ = "group_owners"
    id: Mapped[UUID] = uuid_pk(); group_id: Mapped[UUID] = mapped_column(ForeignKey("groups.id"), nullable=False); user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    assigned_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); created_at: Mapped[datetime] = created_at()
    __table_args__ = (UniqueConstraint("group_id", "user_id", name="uq_group_owners_group_user"),)


class AccessPackageOwner(Base):
    """A user accountable for one access package who may manage it from their own portal — but only rename it or
    remove items from it (never add items, change eligibility/approvers, assign, or delete it; see
    app.services.packages.owner_rename_package / owner_remove_item). Set by an Admin when creating/editing the
    package. Also the source of the reviewer suggestion when an Access Review is scoped to that package."""
    __tablename__ = "access_package_owners"
    id: Mapped[UUID] = uuid_pk(); package_id: Mapped[UUID] = mapped_column(ForeignKey("access_packages.id"), nullable=False); user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    assigned_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); created_at: Mapped[datetime] = created_at()
    __table_args__ = (UniqueConstraint("package_id", "user_id", name="uq_access_package_owners_pkg_user"), Index("ix_access_package_owners_user", "user_id"))


class AccessPackageEligibility(Base):
    __tablename__ = "access_package_eligibility"
    id: Mapped[UUID] = uuid_pk(); package_id: Mapped[UUID] = mapped_column(ForeignKey("access_packages.id"), nullable=False); principal_type: Mapped[str] = mapped_column(String(20), nullable=False); principal_id: Mapped[UUID] = mapped_column(Uuid, nullable=False); created_at: Mapped[datetime] = created_at(); __table_args__ = (Index("ix_access_package_eligibility_package", "package_id"), UniqueConstraint("package_id", "principal_type", "principal_id", name="uq_package_eligibility_principal"))


class AccessPackageItem(Base):
    __tablename__ = "access_package_items"
    id: Mapped[UUID] = uuid_pk(); package_id: Mapped[UUID] = mapped_column(ForeignKey("access_packages.id"), nullable=False); resource_type: Mapped[str] = mapped_column(String(50), nullable=False); resource_id: Mapped[UUID] = mapped_column(Uuid, nullable=False); app_role_external_id: Mapped[Optional[str]] = mapped_column(String(100)); created_at: Mapped[datetime] = created_at(); __table_args__ = (Index("ix_access_package_items_package", "package_id"),)


class AccessPackageAssignment(Base):
    __tablename__ = "access_package_assignments"
    id: Mapped[UUID] = uuid_pk(); package_id: Mapped[UUID] = mapped_column(ForeignKey("access_packages.id"), nullable=False); package_assignment_id: Mapped[UUID] = mapped_column(Uuid, nullable=False); assignment_id: Mapped[UUID] = mapped_column(ForeignKey("access_assignments.id"), nullable=False, unique=True); user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False); created_at: Mapped[datetime] = created_at(); __table_args__ = (Index("ix_access_package_assignments_batch", "package_assignment_id"),)


class BusinessRole(Base):
    """A named, owned bundle of real entitlements (see BusinessRoleItem) — the thing an admin actually manages and
    certifies ("Finance Analyst") instead of five separate group memberships. Modeled directly on AccessPackage:
    status follows the same DRAFT -> ACTIVE -> DISABLED -> ARCHIVED lifecycle (archived once it has assignment
    history, exactly like a package — see services.business_roles.delete_business_role), and the approver/
    fallback-approver/fallback_unlock_hours trio is copied verbatim so a role request can reuse the exact same
    approval wiring a package request already has. role_type/risk_level are cosmetic filtering labels only (no
    schema branching per type, no enforcement keyed off risk_level — the same documented choice SoD's own
    `severity` field already makes)."""
    __tablename__ = "business_roles"
    id: Mapped[UUID] = uuid_pk(); name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True); description: Mapped[Optional[str]] = mapped_column(Text)
    role_type: Mapped[str] = mapped_column(String(30), nullable=False, default="BUSINESS", server_default="BUSINESS")
    department: Mapped[Optional[str]] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT", server_default="DRAFT")
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False, default="LOW", server_default="LOW")
    is_privileged: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    default_approver_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); default_fallback_approver_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); fallback_unlock_hours: Mapped[Optional[int]] = mapped_column(Integer)
    review_frequency_days: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at()


class BusinessRoleItem(Base):
    """One real entitlement mapped onto a Business Role — byte-for-byte the same resource-tuple shape as
    AccessPackageItem, resolved through the same _resolve_target() every other resource reference in this app
    uses. it_role_label is a purely cosmetic reference field (e.g. "Finance-L2-ReadWrite") documenting what IT
    itself calls this specific technical access level; nothing reads it except the mapping display. The unique
    constraint enforces the plan's many-to-one decision: one raw entitlement belongs to at most one Business Role
    at a time, so "who holds Finance Analyst" always means the same set."""
    __tablename__ = "business_role_items"
    id: Mapped[UUID] = uuid_pk(); role_id: Mapped[UUID] = mapped_column(ForeignKey("business_roles.id"), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False); resource_id: Mapped[UUID] = mapped_column(Uuid, nullable=False); app_role_external_id: Mapped[Optional[str]] = mapped_column(String(100))
    it_role_label: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = created_at()
    __table_args__ = (Index("ix_business_role_items_role", "role_id"), UniqueConstraint("resource_type", "resource_id", "app_role_external_id", name="uq_business_role_items_resource"))


class BusinessRoleOwner(Base):
    """An AccessPilot-side owner of a Business Role — mirrors GroupOwner/ApplicationOwner exactly, same
    accountability-record-only convention (nothing pushed to any directory)."""
    __tablename__ = "business_role_owners"
    id: Mapped[UUID] = uuid_pk(); role_id: Mapped[UUID] = mapped_column(ForeignKey("business_roles.id"), nullable=False); user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    assigned_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); created_at: Mapped[datetime] = created_at()
    __table_args__ = (UniqueConstraint("role_id", "user_id", name="uq_business_role_owners_role_user"),)


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
    real_accounts_provisioned_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0"); birthright_assignments_created_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0"); birthright_assignments_revoked_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0"); moves_scheduled_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    uploaded_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); error_summary: Mapped[Optional[dict]] = mapped_column("error_summary", JSON)
    created_at: Mapped[datetime] = created_at(); completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_onboarding_imports_provider", "provider_id"),)


class BirthrightPolicy(Base):
    """Two modes, distinguished purely by whether conditions_json is set (never both at once — see
    app.services.birthright's JSON create/update, which always clears the legacy match_field/resource_type
    columns the moment a policy is saved via JSON):
    - Legacy/simple (match_field/match_value/resource_type/resource_id/app_role_external_id): the original
      single-condition, single-grant shape, unchanged since this table's first migration.
    - JSON/advanced (conditions_json/conditions_operator/actions_json): an AND/OR list of conditions and a list
      of grant actions, authored either via the JSON create/edit endpoints or by editing a legacy policy as JSON
      (which converts it to this mode on save). Every existing/legacy row keeps working exactly as before —
      evaluate_birthright_policies/reconcile_birthright_policies_for_user both branch on conditions_json's
      presence, never on any migration/backfill of old rows."""
    __tablename__ = "birthright_policies"
    id: Mapped[UUID] = uuid_pk(); name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True); match_field: Mapped[Optional[str]] = mapped_column(String(50)); match_value: Mapped[Optional[str]] = mapped_column(String(255))
    resource_type: Mapped[Optional[str]] = mapped_column(String(50)); resource_id: Mapped[Optional[UUID]] = mapped_column(Uuid); app_role_external_id: Mapped[Optional[str]] = mapped_column(String(100)); assignment_type: Mapped[str] = mapped_column(String(50), nullable=False, default="PERMANENT")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="ACTIVE"); created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at()
    # A human-readable business id (the JSON shape's "policyId", e.g. "BR-001") — purely cosmetic, never used to
    # look anything up internally (the real UUID `id` above is what every FK/API path uses); unique only when set.
    external_policy_id: Mapped[Optional[str]] = mapped_column(String(100), unique=True)
    # Stored but not yet enforced beyond the existing, unconditional "PU/TU accounts never match any birthright
    # policy" rule (see evaluate_birthright_policies) — kept for JSON round-trip fidelity with the requested
    # schema, deliberately not reopening that already-settled exclusion based on this field's value.
    scope_identity_type: Mapped[Optional[str]] = mapped_column(String(50))
    conditions_json: Mapped[Optional[list]] = mapped_column("conditions_json", JSON)
    conditions_operator: Mapped[str] = mapped_column(String(10), nullable=False, default="AND", server_default="AND")
    actions_json: Mapped[Optional[list]] = mapped_column("actions_json", JSON)
    # True (the default, matching every pre-existing policy's actual behavior) means reconcile_birthright_policies_
    # for_user auto-revokes this policy's grants the moment its condition stops matching (or the policy is
    # disabled) — exactly what's always happened. False makes a grant "sticky": once given, reconciliation never
    # takes it back on its own, only a manual Admin revoke can. Maps to the requested JSON shape's
    # reconciliation.enabled/removeWhenConditionFails, which this app treats as one and the same knob.
    reconciliation_enabled: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    __table_args__ = (Index("ix_birthright_policies_match", "match_field", "match_value"),)

    # Plain Python properties (not mapped columns) so BirthrightPolicyResponse's from_attributes lookup can read
    # them directly off the ORM row — lets the list/detail API surface "is this a JSON/advanced-mode policy, and
    # how many conditions/actions does it have" without every caller re-deriving it from the raw JSON columns.
    @property
    def is_advanced(self) -> bool:
        return bool(self.conditions_json)

    @property
    def conditions_count(self) -> int:
        return len(self.conditions_json) if self.conditions_json else (1 if self.match_field else 0)

    @property
    def actions_count(self) -> int:
        return len(self.actions_json) if self.actions_json else (1 if self.resource_type else 0)


class Department(Base):
    """A small, Admin-managed lookup list of real department names (see app.services.departments) — populates
    the dropdown on the User Detail page's Department field, so an Admin picks from a known, consistent set
    instead of free-typing a value that might not match what a birthright policy's own department condition
    expects. Purely a picker convenience: User.department itself stays a plain string column, unconstrained by
    this table, so an existing user's department value is never hidden or blocked even if it isn't in this list
    (e.g. one set before this list existed, or synced from Entra)."""
    __tablename__ = "departments"
    id: Mapped[UUID] = uuid_pk(); name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    # The one manager responsible for this department — a Joiner submission suggests this manager the moment the
    # department is picked (admin can still override). Nullable: a department need not have one set yet.
    manager_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at()


class GroupLabel(Base):
    """A small, Admin-managed lookup list of custom Group classification names — same "picker convenience,
    never blocks an out-of-list value" shape as Department (see app.services.group_labels). Sits alongside the
    two built-in classifications (Standard/Privileged) a Group's own group_label column can also hold — this
    table only ever supplies the admin-defined CUSTOM options."""
    __tablename__ = "group_labels"
    id: Mapped[UUID] = uuid_pk(); name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    created_at: Mapped[datetime] = created_at()


class GroupRoleMapping(Base):
    """The group-membership analog of BirthrightPolicy: instead of triggering off a user attribute
    (department/job_title), this triggers off CURRENT membership in `source_group_id` — everyone in that group
    is entitled to `resource_type`/`resource_id` (a directory Role, or an Application/app role). Same
    reconciliation shape as birthright (see app.services.group_role_mapping): live-computed eligibility, ELIGIBLE-
    only grants, and safe auto-revocation via the same AccessAssignment.group_role_mapping_id tagging pattern
    birthright_policy_id already established — never touches a manually-granted assignment."""
    __tablename__ = "group_role_mappings"
    id: Mapped[UUID] = uuid_pk(); source_group_id: Mapped[UUID] = mapped_column(ForeignKey("groups.id"), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False); resource_id: Mapped[UUID] = mapped_column(Uuid, nullable=False); app_role_external_id: Mapped[Optional[str]] = mapped_column(String(100)); assignment_type: Mapped[str] = mapped_column(String(50), nullable=False, default="PERMANENT")
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="ACTIVE"); created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at()
    __table_args__ = (Index("ix_group_role_mappings_source_group", "source_group_id"),)


class PrivilegedAccountPolicy(Base):
    """One row per account_type (PU, PU/TU — get-or-create-on-first-read, same singleton-per-key convention
    SecuritySettings uses for its one row). default_approver_id is None means 'auto-provision immediately, no
    approval needed' (the diagram's "ASAP"); set it and a request for that account_type sits PENDING_APPROVAL
    until that approver decides — the exact same optional-approver shape AccessPackage already uses, not a new
    approval engine."""
    __tablename__ = "privileged_account_policies"
    id: Mapped[UUID] = uuid_pk(); account_type: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    default_approver_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at()


class PrivilegedAccountRequest(Base):
    """'Please create me an identity', not 'please grant me a resource' — structurally distinct from
    AccessAssignment on purpose (see app.services.privileged_accounts). status ELIGIBLE-style lifecycle doesn't
    apply here: PENDING_APPROVAL -> APPROVED/REJECTED, and APPROVED transitions straight to PROVISIONED (or
    FAILED, if the real Entra/Okta create call itself fails) in the same action as the approval decision."""
    __tablename__ = "privileged_account_requests"
    id: Mapped[UUID] = uuid_pk(); requester_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False); account_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="PENDING_APPROVAL"); approver_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id"))
    justification: Mapped[Optional[str]] = mapped_column(Text)
    provisioned_user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id"))
    failure_reason: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at(); decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_privileged_account_requests_requester", "requester_id"),)


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
    # Shown on the public sign-in screen (via a dedicated public endpoint, no auth — the whole point is a user who
    # can't sign in at all still needs to see it) whenever the IDP itself can't be reached (e.g. no network route
    # to it) — a real person to contact instead of a dead end. Nullable: unset means the sign-in screen falls back
    # to a generic "contact your administrator" with no address, exactly like before this field existed.
    support_contact_email: Mapped[Optional[str]] = mapped_column(String(255))
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


class SocDashboardLayout(Base):
    """One row per AccessPilot.SoCAdmin (or Admin) who has customized their Security Operations dashboard —
    the FIRST genuinely per-viewer preference table in this app (every other settings table — SecuritySettings,
    BrandingSettings, SodNotificationSettings — is a shared singleton). `widgets` is a plain JSON list of
    `{id, visible, order}` objects, one per known widget id the frontend already knows how to render (the set of
    available widgets/metrics is fixed in code for this first version, not admin-defined — this column only
    controls which ones a given viewer sees and in what order, not what a widget IS). No row for a viewer means
    "never customized yet" — the frontend falls back to a hardcoded default (all widgets visible, default order)
    rather than this table needing a pre-seeded default row."""
    __tablename__ = "soc_dashboard_layouts"
    id: Mapped[UUID] = uuid_pk()
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, unique=True)
    widgets: Mapped[list] = mapped_column("widgets", JSON, nullable=False)
    updated_at: Mapped[datetime] = updated_at()


class AccessReviewCampaign(Base):
    """A periodic access-recertification run — snapshots whatever AccessAssignment rows match its scope into
    AccessReviewItem rows at creation time (see app.services.access_reviews), then closes when every item has a
    decision, either by hand or via the 60s access-review worker's auto-revoke-on-due-date sweep. Deliberately a
    ONE-TIME SNAPSHOT of a fixed roster, not live-recomputed like SoD/birthright — a review certifies who had
    access as of the moment it started, not a moving target. reviewer_id/fallback_reviewer_id/fallback_unlock_hours
    is the exact same two-tier approver shape AccessPackage/AccessAssignment/SodExceptionRequest already use."""
    __tablename__ = "access_review_campaigns"
    id: Mapped[UUID] = uuid_pk(); name: Mapped[str] = mapped_column(String(255), nullable=False); description: Mapped[Optional[str]] = mapped_column(Text)
    scope_type: Mapped[str] = mapped_column(String(30), nullable=False)
    scope_resource_type: Mapped[Optional[str]] = mapped_column(String(50)); scope_resource_id: Mapped[Optional[UUID]] = mapped_column(Uuid)
    # Only set when scope_type is MULTIPLE_RESOURCES — a list of {"resource_type", "resource_id"} dicts, mixing
    # resource types freely (e.g. a Group and a Package reviewed together in one campaign). scope_resource_type/
    # scope_resource_id above stay NULL in that case; they're mutually exclusive with this column, never both set.
    scope_targets: Mapped[Optional[list]] = mapped_column("scope_targets", JSON)
    # scope_inactive_days is only set when scope_type is INACTIVE_USERS — the "no sign-in in N days" threshold
    # (see app.services.access_reviews._resolve_inactive_user_ids); every other scope type leaves it NULL.
    scope_inactive_days: Mapped[Optional[int]] = mapped_column(Integer)
    scope_user_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); scope_account_type: Mapped[Optional[str]] = mapped_column(String(20))
    # Exactly one of reviewer_id / workflow_definition_id is set (see AccessReviewCampaignCreate's validator) — a
    # campaign always needs something deciding it. When workflow-routed, fallback_reviewer_id/fallback_unlock_hours
    # stay NULL (the workflow has its own per-stage fallback/escalation instead).
    reviewer_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id"))
    fallback_reviewer_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); fallback_unlock_hours: Mapped[Optional[int]] = mapped_column(Integer)
    workflow_definition_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("workflow_definitions.id"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE", server_default="ACTIVE")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # frequency_days: NULL means one-time. Set means "recurring" — when this campaign completes (manually, via
    # every-item-decided, or via the overdue-sweep worker), a fresh campaign with the same scope/reviewer is
    # automatically spawned, due frequency_days from then, linked back via parent_campaign_id (see
    # app.services.access_reviews._maybe_spawn_recurrence). Deliberately NOT retroactive to already-created items.
    frequency_days: Mapped[Optional[int]] = mapped_column(Integer)
    # What happens to items still PENDING at the due date: REVOKE (auto-revoke, the original behaviour) or KEEP (auto-approve).
    on_no_response: Mapped[str] = mapped_column(String(10), nullable=False, default="REVOKE", server_default="REVOKE")
    # Fixed-calendar recurrence (alternative to frequency_days, never both): a NEW campaign STARTS on
    # schedule_day_of_month (1-31, clamped to short months) at schedule_time ("HH:MM", in the app's configured
    # timezone) every schedule_every_months months, staying open for schedule_due_days. next_run_at (UTC) is
    # held by the newest campaign in the chain only; see app.services.access_reviews.sweep_scheduled_campaigns.
    schedule_day_of_month: Mapped[Optional[int]] = mapped_column(Integer)
    schedule_time: Mapped[Optional[str]] = mapped_column(String(5))
    schedule_every_months: Mapped[Optional[int]] = mapped_column(Integer)
    schedule_due_days: Mapped[Optional[int]] = mapped_column(Integer)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    parent_campaign_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("access_review_campaigns.id"))
    created_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at(); completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_access_review_campaigns_status", "status"),)


class AccessReviewItem(Base):
    """One row per AccessAssignment swept into a campaign at snapshot time. resource_type/resource_id/
    app_role_external_id are denormalized copies (not a live join) so the line item still displays correctly
    even if the underlying assignment is later revoked, or the resource itself renamed/deleted, after the
    snapshot. decision APPROVED never touches the underlying AccessAssignment at all (certifying existing access
    isn't the same as (re)granting it); decision REVOKED/AUTO_REVOKED calls the same, unmodified
    app.services.assignments.revoke_assignment() every other admin revoke path already uses."""
    __tablename__ = "access_review_items"
    id: Mapped[UUID] = uuid_pk(); campaign_id: Mapped[UUID] = mapped_column(ForeignKey("access_review_campaigns.id"), nullable=False)
    assignment_id: Mapped[UUID] = mapped_column(ForeignKey("access_assignments.id"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False); resource_id: Mapped[UUID] = mapped_column(Uuid, nullable=False); app_role_external_id: Mapped[Optional[str]] = mapped_column(String(100))
    assignment_status_at_snapshot: Mapped[str] = mapped_column(String(50), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", server_default="PENDING")
    decided_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id")); decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    justification: Mapped[Optional[str]] = mapped_column(Text)
    # Set only when the owning campaign is workflow-routed — this item's own independent WorkflowInstance (one per
    # item, not one shared per campaign), so each reviewed grant gets its own stage-by-stage decision.
    workflow_instance_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("workflow_instances.id"))
    created_at: Mapped[datetime] = created_at()
    __table_args__ = (Index("ix_access_review_items_campaign", "campaign_id"), Index("ix_access_review_items_decision", "decision"))


class WorkflowDefinition(Base):
    """A named, admin-authored multi-stage approval workflow — see app.services.workflows for the full engine.
    Deliberately standalone: nothing existing is wired to this (see WorkflowRequest, the one new 'thing a user
    submits' that exercises it). status ACTIVE is required before a request can be submitted against it."""
    __tablename__ = "workflow_definitions"
    id: Mapped[UUID] = uuid_pk(); name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True); description: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT", server_default="DRAFT")
    created_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at()


class WorkflowStageDefinition(Base):
    """One ordered stage of a WorkflowDefinition. approver_user_ids/fallback_approver_ids are JSON arrays of UUID
    strings — the same 'explicit array of equally-authorized deciders' shape LeaverRequest.approver_ids already
    uses. condition_field/operator/value mirror BirthrightPolicy's condition shape exactly (field, EQUALS/
    NOT_EQUALS, value) — a stage with no condition always activates; one with a condition only activates if the
    owning WorkflowInstance's payload matches it, otherwise it's skipped (see services.workflows)."""
    __tablename__ = "workflow_stage_definitions"
    id: Mapped[UUID] = uuid_pk(); workflow_definition_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_definitions.id"), nullable=False)
    stage_number: Mapped[int] = mapped_column(Integer, nullable=False); name: Mapped[str] = mapped_column(String(255), nullable=False)
    approval_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="ANY_OF", server_default="ANY_OF")
    approver_user_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    fallback_approver_ids: Mapped[Optional[list]] = mapped_column(JSON)
    escalate_after_hours: Mapped[Optional[int]] = mapped_column(Integer)
    condition_field: Mapped[Optional[str]] = mapped_column(String(50)); condition_operator: Mapped[Optional[str]] = mapped_column(String(20)); condition_value: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = created_at()
    __table_args__ = (Index("ix_workflow_stage_definitions_definition", "workflow_definition_id"), UniqueConstraint("workflow_definition_id", "stage_number", name="uq_workflow_stage_definitions_number"))


class WorkflowInstance(Base):
    """One running/completed workflow. subject_type/subject_id exist so a future feature could attach its own
    instance without a schema change; everything built in this pass sets subject_type='WORKFLOW_REQUEST' and
    subject_id = that WorkflowRequest's own id. payload is the arbitrary data a stage's condition evaluates
    against. current_stage_number is the stage currently awaiting a decision (or the last one reached, once
    completed) — stages are created lazily, see WorkflowStageInstance and services.workflows."""
    __tablename__ = "workflow_instances"
    id: Mapped[UUID] = uuid_pk(); workflow_definition_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_definitions.id"), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(50), nullable=False); subject_id: Mapped[Optional[UUID]] = mapped_column(Uuid)
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    payload: Mapped[Optional[dict]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", server_default="PENDING")
    current_stage_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    started_at: Mapped[datetime] = created_at(); completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_workflow_instances_definition", "workflow_definition_id"), Index("ix_workflow_instances_status", "status"))


class WorkflowStageInstance(Base):
    """One per stage an instance actually reaches — created lazily (never all-upfront), so a stage never reached
    (the instance was rejected/cancelled before it, or a later stage was skipped) simply has no row; that absence
    IS the record, not a status value. name/approval_mode/required_approver_ids are snapshots taken at the moment
    this stage was entered, so a later edit to the owning WorkflowDefinition never rewrites in-flight or historical
    instances. escalates_at is the configured threshold; escalated_at is set once (and only once) the escalation
    sweep actually acts on it — see app.workers.workflow_escalation."""
    __tablename__ = "workflow_stage_instances"
    id: Mapped[UUID] = uuid_pk(); workflow_instance_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_instances.id"), nullable=False)
    stage_number: Mapped[int] = mapped_column(Integer, nullable=False); name: Mapped[str] = mapped_column(String(255), nullable=False)
    approval_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    required_approver_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    fallback_approver_ids: Mapped[Optional[list]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", server_default="PENDING")
    escalates_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True)); escalated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at(); completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_workflow_stage_instances_instance", "workflow_instance_id"), Index("ix_workflow_stage_instances_status", "status"), Index("ix_workflow_stage_instances_escalation", "status", "escalates_at", "escalated_at"))


class WorkflowStageDecision(Base):
    """One row per individual approver's vote on a WorkflowStageInstance — needed so ALL_OF can require every
    named approver and still show who voted which way. A unique constraint on (stage, decided_by) enforces one
    decision per user per stage at the DB level; services.workflows separately enforces that once the stage
    itself is no longer PENDING, no further decisions are accepted from anyone — there is no retraction/re-voting."""
    __tablename__ = "workflow_stage_decisions"
    id: Mapped[UUID] = uuid_pk(); workflow_stage_instance_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_stage_instances.id"), nullable=False)
    decided_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False); justification: Mapped[Optional[str]] = mapped_column(Text)
    decided_at: Mapped[datetime] = created_at()
    __table_args__ = (Index("ix_workflow_stage_decisions_stage", "workflow_stage_instance_id"), UniqueConstraint("workflow_stage_instance_id", "decided_by", name="uq_workflow_stage_decisions_once"))


class WorkflowRequest(Base):
    """The standalone 'thing a user submits' that exercises the workflow engine end to end — kept as its own
    table (not folded into WorkflowInstance) so the engine's core tables stay generic and reusable while this one
    stays obviously specific to this pilot entry point. 1:1 with the WorkflowInstance it drives."""
    __tablename__ = "workflow_requests"
    id: Mapped[UUID] = uuid_pk(); workflow_definition_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_definitions.id"), nullable=False)
    workflow_instance_id: Mapped[UUID] = mapped_column(ForeignKey("workflow_instances.id"), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False); description: Mapped[Optional[str]] = mapped_column(Text); justification: Mapped[Optional[str]] = mapped_column(Text)
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = created_at()
    __table_args__ = (Index("ix_workflow_requests_requested_by", "requested_by"),)


class EntitlementCatalogEntry(Base):
    """A plain-English description, risk tier, and accountable owner for one real entitlement — a whole Group/
    Role, or one specific AppRole on an Application — independent of whether it's bundled into a Package or
    Business Role yet (see app.services.entitlement_catalog). Sits ALONGSIDE the existing per-resource-type owner
    tables (GroupOwner/ApplicationOwner/BusinessRoleOwner/AccessPackageOwner): those answer "who's accountable for
    this container," this answers "what does this specific entitlement mean and how risky is it." A dedicated
    table (not columns bolted onto Group/Role) because an Application's AppRoles only exist as JSON on
    Application.app_roles with no row of their own to extend — this is the only way to address one individually.
    app_role_external_id is a non-nullable empty string (not NULL) when the entry describes a whole Group/Role/
    Application rather than one specific AppRole, so the unique constraint below actually enforces one entry per
    real entitlement — Postgres treats NULL <> NULL in a unique constraint, which would otherwise silently allow
    duplicate rows for the same Group/Role."""
    __tablename__ = "entitlement_catalog_entries"
    id: Mapped[UUID] = uuid_pk(); resource_type: Mapped[str] = mapped_column(String(50), nullable=False); resource_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    app_role_external_id: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    description: Mapped[Optional[str]] = mapped_column(Text); risk_tier: Mapped[str] = mapped_column(String(20), nullable=False, default="LOW", server_default="LOW")
    owner_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created_at(); updated_at: Mapped[datetime] = updated_at()
    __table_args__ = (UniqueConstraint("resource_type", "resource_id", "app_role_external_id", name="uq_entitlement_catalog_resource"),)
