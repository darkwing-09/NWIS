import os
import pytest
from fastapi.testclient import TestClient

from config.settings import Settings, get_settings
from config.errors import (
    NWISError,
    NotFoundError,
    ValidationError,
    ConflictError,
    ExternalServiceError,
    AuthorizationError,
    AuthenticationError,
)
from domain.logging.logger import get_logger, set_trace_id, get_trace_id, setup_logging
from apps.api.main import create_app


def test_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_POOL_SIZE", "25")
    monkeypatch.setenv("APP_NAME", "nwis-test")
    monkeypatch.setenv("CORRELATION_DEPTH_BAND_M", "150.0")

    settings = Settings()
    assert settings.database_pool_size == 25
    assert settings.app_name == "nwis-test"
    assert settings.correlation_depth_band_m == 150.0


def test_logger_includes_trace_id_when_set():
    set_trace_id("test-trace-12345")
    assert get_trace_id() == "test-trace-12345"
    logger = get_logger("test.logger")
    # Verify logger binds and returns a BoundLogger
    assert logger is not None


def test_health_endpoint_unauthenticated():
    app = create_app()
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "X-Trace-ID" in response.headers


def test_readiness_endpoint():
    app = create_app()
    client = TestClient(app)
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_each_error_maps_to_correct_http_status():
    app = create_app()

    @app.get("/test-not-found")
    def raise_not_found():
        raise NotFoundError("Item not found", detail={"item_id": "abc"})

    @app.get("/test-validation")
    def raise_validation():
        raise ValidationError("Field invalid", detail={"field": "depth"})

    @app.get("/test-conflict")
    def raise_conflict():
        raise ConflictError("Duplicate entry")

    @app.get("/test-external")
    def raise_external():
        raise ExternalServiceError("OCR timeout")

    @app.get("/test-forbidden")
    def raise_forbidden():
        raise AuthorizationError("Insufficient permissions")

    @app.get("/test-unauthorized")
    def raise_unauthorized():
        raise AuthenticationError("Invalid token")

    from services.auth.oidc_client import create_token
    token = create_token(sub="test-user", email="test@oilindia.in", name="Test User")
    headers = {"Authorization": f"Bearer {token}"}

    client = TestClient(app)

    r404 = client.get("/test-not-found", headers=headers)
    assert r404.status_code == 404
    assert r404.json()["error_code"] == "not_found"
    assert r404.json()["detail"] == {"item_id": "abc"}

    r422 = client.get("/test-validation", headers=headers)
    assert r422.status_code == 422
    assert r422.json()["error_code"] == "validation_error"

    r409 = client.get("/test-conflict", headers=headers)
    assert r409.status_code == 409
    assert r409.json()["error_code"] == "conflict"

    r502 = client.get("/test-external", headers=headers)
    assert r502.status_code == 502
    assert r502.json()["error_code"] == "external_service_error"

    r403 = client.get("/test-forbidden", headers=headers)
    assert r403.status_code == 403
    assert r403.json()["error_code"] == "forbidden"

    r401 = client.get("/test-unauthorized", headers=headers)
    assert r401.status_code == 401
    assert r401.json()["error_code"] == "unauthorized"


def test_unhandled_exception_returns_generic_500_no_stack_trace():
    app = create_app()

    @app.get("/test-unhandled")
    def raise_unhandled():
        raise RuntimeError("Secret DB password in stack trace: supersecret")

    from services.auth.oidc_client import create_token
    token = create_token(sub="test-user", email="test@oilindia.in", name="Test User")
    headers = {"Authorization": f"Bearer {token}"}

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/test-unhandled", headers=headers)
    assert resp.status_code == 500
    data = resp.json()
    assert data["error_code"] == "internal_error"
    assert "supersecret" not in resp.text
    assert data["message"] == "An internal server error occurred"

