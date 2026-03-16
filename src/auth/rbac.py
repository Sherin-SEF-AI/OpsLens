"""Role-based access control for OpsLens."""

from __future__ import annotations

import enum
from functools import wraps
from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, status

from src.database.models import UserRole

if TYPE_CHECKING:
    from src.database.models import User


class Permission(str, enum.Enum):
    """Fine-grained permissions used across OpsLens."""

    VIEW_INCIDENTS = "view_incidents"
    CREATE_INCIDENTS = "create_incidents"
    TRANSITION_INCIDENTS = "transition_incidents"
    COMMENT_INCIDENTS = "comment_incidents"
    RUN_COMMANDER = "run_commander"
    MANAGE_INTEGRATIONS = "manage_integrations"
    MANAGE_SETTINGS = "manage_settings"
    MANAGE_USERS = "manage_users"
    MANAGE_ONCALL = "manage_oncall"
    MANAGE_SLA = "manage_sla"
    MANAGE_ALERT_RULES = "manage_alert_rules"
    VIEW_AUDIT = "view_audit"
    EXECUTE_RUNBOOKS = "execute_runbooks"
    GENERATE_REPORTS = "generate_reports"


# Hierarchical permission sets.  Each higher role includes all permissions of
# the roles below it, plus its own.
_VIEWER_PERMS: set[Permission] = {
    Permission.VIEW_INCIDENTS,
}

_RESPONDER_PERMS: set[Permission] = _VIEWER_PERMS | {
    Permission.CREATE_INCIDENTS,
    Permission.TRANSITION_INCIDENTS,
    Permission.COMMENT_INCIDENTS,
    Permission.EXECUTE_RUNBOOKS,
}

_COMMANDER_PERMS: set[Permission] = _RESPONDER_PERMS | {
    Permission.RUN_COMMANDER,
    Permission.MANAGE_ONCALL,
    Permission.MANAGE_ALERT_RULES,
    Permission.GENERATE_REPORTS,
    Permission.VIEW_AUDIT,
}

_ADMIN_PERMS: set[Permission] = _COMMANDER_PERMS | {
    Permission.MANAGE_INTEGRATIONS,
    Permission.MANAGE_SETTINGS,
    Permission.MANAGE_USERS,
    Permission.MANAGE_SLA,
}

ROLE_PERMISSIONS: dict[UserRole, set[Permission]] = {
    UserRole.VIEWER: _VIEWER_PERMS,
    UserRole.RESPONDER: _RESPONDER_PERMS,
    UserRole.COMMANDER: _COMMANDER_PERMS,
    UserRole.ADMIN: _ADMIN_PERMS,
}

# Role hierarchy for ordering comparisons (higher number = more privileged)
ROLE_HIERARCHY: dict[UserRole, int] = {
    UserRole.VIEWER: 0,
    UserRole.RESPONDER: 1,
    UserRole.COMMANDER: 2,
    UserRole.ADMIN: 3,
}


def has_permission(role: UserRole, permission: Permission) -> bool:
    """Check whether a role includes a given permission.

    Args:
        role: The user's role.
        permission: The permission to test.

    Returns:
        ``True`` if the role grants the permission.
    """
    return permission in ROLE_PERMISSIONS.get(role, set())


def require_permission(permission: Permission):
    """FastAPI dependency factory that enforces a specific permission.

    Usage::

        @router.post("/incidents")
        async def create_incident(
            user: User = Depends(require_permission(Permission.CREATE_INCIDENTS)),
        ):
            ...

    Args:
        permission: The permission the caller must possess.

    Returns:
        A FastAPI ``Depends``-compatible callable that resolves to the
        authenticated ``User`` or raises ``403``.
    """
    from src.auth.middleware import get_current_active_user  # deferred to avoid circular

    async def _checker(
        current_user: "User" = Depends(get_current_active_user),
    ) -> "User":
        if not has_permission(current_user.role, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: {permission.value} required",
            )
        return current_user

    return _checker
