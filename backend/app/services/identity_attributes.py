from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import IdentityProvider, User
from app.providers.graph_client import GraphError
from app.services.audit import record_audit
from app.services.birthright import reconcile_birthright_policies_for_user
from app.services.provider_configuration import _connector


async def apply_user_attribute_change(session: AsyncSession, user_id: UUID, department: Optional[str], job_title: Optional[str], actor_subject: str, request_id: str, *, via_workflow: bool = False) -> User:
    """The real write: pushes department/job_title to the identity's own provider (Entra/Okta stays the source of
    truth, never just a local-only edit), updates the local row, audits it, then runs the same birthright mover
    reconciliation a directory sync would trigger for the same change arriving from the other direction. Shared by
    the instant admin-edit path (api.v1.directory.update_user_attributes) and the workflow-approved path
    (services.workflows's USER_ATTRIBUTES completion branch) so both apply the exact same sequence."""
    user = await session.get(User, user_id)
    if user is None:
        raise AccessPilotError("USER_NOT_FOUND", "The user was not found.", 404)
    provider = await session.get(IdentityProvider, user.provider_id)
    if provider is None:
        raise AccessPilotError("PROVIDER_NOT_FOUND", "The provider for this user was not found.", 404)
    connector = _connector(provider)
    try:
        updated = await connector.update_user(user.external_id, department=department, job_title=job_title)
    except GraphError as exc:
        raise AccessPilotError(exc.code, exc.message, exc.status_code) from exc
    attributes_changed = user.department != updated.department or user.job_title != updated.job_title
    previous_attributes = {"department": user.department, "job_title": user.job_title}
    user.department, user.job_title = updated.department, updated.job_title
    await record_audit(session, action="USER_ATTRIBUTES_UPDATED", target_type="USER", target_id=user.id, provider_id=provider.id, request_id=request_id, metadata={"department": updated.department, "job_title": updated.job_title, "via_workflow": via_workflow})
    await session.commit()
    await session.refresh(user)
    if attributes_changed:
        # Reconciliation records the actor who ultimately caused this edit (the admin, or — once approved via a
        # workflow — whichever resolution path provided an actor_subject) rather than "system", unlike a
        # directory-sync-triggered reconciliation.
        outcome = await reconcile_birthright_policies_for_user(session, user.id, actor_subject, request_id)
        from app.services.lifecycle import build_changes, record_mover
        await record_mover(session, user.id, build_changes(previous_attributes, {"department": user.department, "job_title": user.job_title}), "ADMIN_EDIT", outcome, request_id)
    return user
