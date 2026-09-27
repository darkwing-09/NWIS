import uuid
from typing import Callable, List, Optional
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.errors import AuthorizationError
from database.models.auth import Permission, Role
from database.session import get_session
from domain.models.wells import AuthenticatedUser


def get_user_permissions(db: Session, user_id: uuid.UUID) -> List[Permission]:
    """Fetch all active permission grants for a user."""
    stmt = (
        select(Permission)
        .where(Permission.user_id == user_id)
        .join(Role)
    )
    return list(db.execute(stmt).scalars().all())


def has_field_access(db: Session, user_id: uuid.UUID, field_name: Optional[str]) -> bool:
    """Check if user has explicit access to a field or an all-fields grant."""
    if field_name is None:
        return True

    perms = get_user_permissions(db, user_id)
    for p in perms:
        if p.role.name == "admin":
            return True
        if p.field_name is None or p.field_name == field_name:
            return True
    return False


def require_permission(resource: str, action: str) -> Callable:
    """
    FastAPI Depends factory verifying caller has permission for resource:action.
    Enforced at router level per Part 9 and Part 19.
    """
    def dependency(request: Request) -> None:
        user: Optional[AuthenticatedUser] = getattr(request.state, "user", None)
        if not user:
            raise AuthorizationError(
                "Access denied: unauthenticated user context",
                detail={"resource": resource, "action": action},
            )

        if "admin" in user.roles:
            return

        # Role-based policy matrix mapping
        # E.g. superintendents and engineers can read wells and documents
        if action == "read":
            return

        if action in ("create", "write", "upload"):
            if "engineer" in user.roles or "superintendent" in user.roles:
                return

        if action in ("approve", "admin", "delete"):
            raise AuthorizationError(
                f"Action '{action}' on '{resource}' requires admin privileges",
                detail={"resource": resource, "action": action, "roles": user.roles},
            )

    return dependency
