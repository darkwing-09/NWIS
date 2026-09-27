from typing import Optional
from fastapi import Depends, Request

from config.errors import AuthenticationError, AuthorizationError
from domain.models.wells import AuthenticatedUser


def get_current_user(request: Request) -> AuthenticatedUser:
    """FastAPI dependency to extract AuthenticatedUser attached by AuthMiddleware."""
    user: Optional[AuthenticatedUser] = getattr(request.state, "user", None)
    if not user:
        raise AuthenticationError("User is not authenticated", detail={})
    return user
