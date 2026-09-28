from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import User
from app.services.audit import record_audit


async def get_hierarchy_tree(session: AsyncSession) -> list[User]:
    """Every user, flat — the frontend assembles the actual tree from `manager_id` client-side (no recursive CTE
    needed; tenant sizes here are small enough that this is simpler and just as fast)."""
    return list((await session.scalars(select(User).order_by(User.display_name))).all())


async def _would_create_cycle(session: AsyncSession, user_id: UUID, proposed_manager_id: UUID) -> bool:
    """Walks the proposed manager's own chain of managers upward; if user_id appears anywhere in that chain,
    assigning it would create a cycle (user_id would end up, directly or indirectly, managing their own
    manager). Bounded by a real, finite walk — `manager_id` chains can't be longer than the number of users."""
    current_id: Optional[UUID] = proposed_manager_id
    seen: set[UUID] = set()
    while current_id is not None:
        if current_id == user_id:
            return True
        if current_id in seen:
            break  # an existing cycle elsewhere in the data — not this write's problem, stop walking
        seen.add(current_id)
        current = await session.get(User, current_id)
        current_id = current.manager_id if current else None
    return False


async def update_user_hierarchy(session: AsyncSession, user_id: UUID, data, actor_id: Optional[UUID], request_id: str) -> User:
    """Purely a local DB write — no real Entra/Graph call, unlike the department/job-title attribute edit this
    sits next to on the User Detail page (that one deliberately pushes a real write to the directory first).
    This hierarchy is AccessPilot-internal only, never synced to/from Entra's own native `manager` relationship."""
    user = await session.get(User, user_id)
    if user is None:
        raise AccessPilotError("USER_NOT_FOUND", "The user was not found.", 404)

    previous_manager_id = user.manager_id
    changes: dict[str, object] = {}
    if data.clear_employee_category:
        user.employee_category = None
        changes["employee_category"] = None
    elif data.employee_category is not None:
        user.employee_category = data.employee_category
        changes["employee_category"] = data.employee_category

    if data.clear_manager:
        user.manager_id = None
        changes["manager_id"] = None
    elif data.manager_id is not None:
        if data.manager_id == user_id:
            raise AccessPilotError("MANAGER_CYCLE_DETECTED", "A user cannot be their own manager.", 400)
        manager = await session.get(User, data.manager_id)
        if manager is None:
            raise AccessPilotError("USER_NOT_FOUND", "The selected manager was not found.", 404)
        if await _would_create_cycle(session, user_id, data.manager_id):
            raise AccessPilotError("MANAGER_CYCLE_DETECTED", "This assignment would create a management cycle.", 400)
        user.manager_id = data.manager_id
        changes["manager_id"] = str(data.manager_id)

    if changes:
        await record_audit(session, action="USER_HIERARCHY_UPDATED", target_type="USER", target_id=user.id, actor_user_id=actor_id, request_id=request_id, metadata=changes)
    await session.commit()
    if user.manager_id != previous_manager_id:
        # Tell the person and both managers — a manager change shifts who reviews their access.
        from app.services.notifications import create_notification
        recipients = {user.id: "Your manager was changed." if user.manager_id else "Your manager was removed."}
        if user.manager_id:
            recipients[user.manager_id] = f"{user.display_name} now reports to you."
        if previous_manager_id:
            recipients[previous_manager_id] = f"{user.display_name} no longer reports to you."
        for recipient_id, message in recipients.items():
            await create_notification(session, recipient_id, "MANAGER_CHANGED", message, "/my-access")
        await session.commit()
    await session.refresh(user)
    return user
