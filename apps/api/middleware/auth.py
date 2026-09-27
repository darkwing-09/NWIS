from typing import Callable, Set
from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from config.errors import AuthenticationError
from domain.logging.logger import get_logger
from domain.models.wells import AuthenticatedUser
from services.auth.oidc_client import validate_token

logger = get_logger("apps.api.middleware.auth")

PUBLIC_PATHS: Set[str] = {
    "/health",
    "/ready",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/login",
}


class AuthMiddleware(BaseHTTPMiddleware):
    """Enforces JWT authentication across protected routes and attaches AuthenticatedUser."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Check if route is public
        if request.url.path in PUBLIC_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        if not auth_header:
            logger.warning("Unauthenticated request to protected route", path=request.url.path)
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "error_code": "unauthorized",
                    "message": "Missing Bearer token in Authorization header",
                    "detail": {"path": request.url.path},
                },
            )

        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "error_code": "unauthorized",
                    "message": "Invalid Authorization header format. Expected 'Bearer <token>'",
                    "detail": {},
                },
            )

        token = parts[1]
        try:
            claims = validate_token(token)
            request.state.claims = claims

            # Construct AuthenticatedUser context
            import uuid
            try:
                user_uuid = uuid.UUID(claims.sub)
            except ValueError:
                user_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, claims.sub)

            auth_user = AuthenticatedUser(
                user_id=user_uuid,
                email=claims.email,
                name=claims.name,
                roles=claims.roles,
                allowed_fields=claims.allowed_fields,
            )
            request.state.user = auth_user
        except AuthenticationError as e:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"error_code": e.code, "message": e.message, "detail": e.detail},
            )
        except Exception as e:
            logger.error("Authentication error", error=str(e))
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"error_code": "unauthorized", "message": "Authentication failed", "detail": {}},
            )

        return await call_next(request)
