from typing import Any, Dict
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from config.errors import (
    NWISError,
    NotFoundError,
    ValidationError,
    ConflictError,
    ExternalServiceError,
    AuthorizationError,
    AuthenticationError,
    CorruptFileError,
    OCRProcessingError,
    ModelNotApprovedError,
)
from domain.logging.logger import get_logger, setup_logging
from apps.api.middleware.request_context import RequestContextMiddleware
from apps.api.middleware.auth import AuthMiddleware
from apps.api.routers.health import router as health_router
from apps.api.routers.audit import router as audit_router
from apps.api.routers.documents import router as documents_router
from apps.api.routers.wells import router as wells_router
from apps.api.routers.risk import router as risk_router
from apps.api.routers.alerts import router as alerts_router
from apps.api.routers.search import router as search_router

logger = get_logger("apps.api.main")


def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(
        title="NWIS API",
        description="Nearby Wells Intelligence System for Oil India Limited",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Middleware: RequestContext is outermost so trace_id is available before auth logs anything
    app.add_middleware(AuthMiddleware)
    app.add_middleware(RequestContextMiddleware)

    # Register Routers
    app.include_router(health_router)
    app.include_router(audit_router)
    app.include_router(documents_router)
    app.include_router(wells_router)
    app.include_router(risk_router)
    app.include_router(alerts_router)
    app.include_router(search_router)

    # Standard Exception Handlers mapping to {error_code, message, detail}
    @app.exception_handler(NotFoundError)
    async def not_found_handler(request: Request, exc: NotFoundError) -> JSONResponse:
        logger.warning("Resource not found", error_code=exc.code, message=exc.message, detail=exc.detail)
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error_code": exc.code, "message": exc.message, "detail": exc.detail},
        )

    @app.exception_handler(ValidationError)
    async def validation_error_handler(request: Request, exc: ValidationError) -> JSONResponse:
        logger.warning("Validation error", error_code=exc.code, message=exc.message, detail=exc.detail)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"error_code": exc.code, "message": exc.message, "detail": exc.detail},
        )

    @app.exception_handler(RequestValidationError)
    async def pydantic_validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        logger.warning("Request schema validation failed", errors=exc.errors())
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error_code": "validation_error",
                "message": "Invalid request parameters",
                "detail": {"errors": exc.errors()},
            },
        )

    @app.exception_handler(ConflictError)
    async def conflict_handler(request: Request, exc: ConflictError) -> JSONResponse:
        logger.warning("Resource conflict", error_code=exc.code, message=exc.message, detail=exc.detail)
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error_code": exc.code, "message": exc.message, "detail": exc.detail},
        )

    @app.exception_handler(AuthenticationError)
    async def authentication_handler(request: Request, exc: AuthenticationError) -> JSONResponse:
        logger.warning("Authentication failure", error_code=exc.code, message=exc.message)
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"error_code": exc.code, "message": exc.message, "detail": exc.detail},
        )

    @app.exception_handler(AuthorizationError)
    async def authorization_handler(request: Request, exc: AuthorizationError) -> JSONResponse:
        logger.warning("Authorization failure", error_code=exc.code, message=exc.message, detail=exc.detail)
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"error_code": exc.code, "message": exc.message, "detail": exc.detail},
        )

    @app.exception_handler(ExternalServiceError)
    async def external_service_handler(request: Request, exc: ExternalServiceError) -> JSONResponse:
        logger.error("External service failure", error_code=exc.code, message=exc.message, detail=exc.detail)
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"error_code": exc.code, "message": exc.message, "detail": exc.detail},
        )

    @app.exception_handler(NWISError)
    async def nwis_base_handler(request: Request, exc: NWISError) -> JSONResponse:
        logger.error("Domain error", error_code=exc.code, message=exc.message, detail=exc.detail)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error_code": exc.code, "message": exc.message, "detail": exc.detail},
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": "http_error",
                "message": exc.detail,
                "detail": {},
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # Never leak raw stack trace to client
        logger.exception("Unhandled server exception", exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error_code": "internal_error",
                "message": "An internal server error occurred",
                "detail": {},
            },
        )

    return app


app = create_app()
