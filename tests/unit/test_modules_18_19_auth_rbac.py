import uuid
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models.base import Base
from domain.models.wells import AuthenticatedUser
from services.auth.oidc_client import create_token, validate_token
from services.auth.user_service import get_or_create_user
from services.auth.rbac import require_permission
from apps.api.main import create_app
from apps.api.middleware.rbac import get_current_user
from config.errors import AuthenticationError, AuthorizationError


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    yield session
    session.close()


def test_valid_token_returns_claims():
    token = create_token(
        sub="user-123",
        email="eng@oilindia.in",
        name="Field Eng",
        roles=["engineer"],
        allowed_fields=["Digboi"],
        expires_in_sec=3600,
    )
    claims = validate_token(token)
    assert claims.sub == "user-123"
    assert claims.email == "eng@oilindia.in"
    assert claims.roles == ["engineer"]
    assert claims.allowed_fields == ["Digboi"]


def test_expired_token_rejected():
    token = create_token(
        sub="user-123",
        email="eng@oilindia.in",
        name="Field Eng",
        expires_in_sec=-10,  # Expired 10s ago
    )
    with pytest.raises(AuthenticationError) as exc_info:
        validate_token(token)
    assert "expired" in str(exc_info.value).lower()


def test_wrong_audience_rejected():
    token = create_token(
        sub="user-123",
        email="eng@oilindia.in",
        name="Field Eng",
        audience="wrong-service-aud",
    )
    with pytest.raises(AuthenticationError) as exc_info:
        validate_token(token)
    assert "audience" in str(exc_info.value).lower()


def test_first_login_creates_user(db_session):
    claims = validate_token(
        create_token(
            sub="new-user-456",
            email="newbie@oilindia.in",
            name="New Engineer",
            roles=["engineer"],
            allowed_fields=["Moran"],
        )
    )
    user = get_or_create_user(db_session, claims)
    assert user.user_id is not None
    assert user.external_idp_id == "new-user-456"
    assert user.email == "newbie@oilindia.in"
    assert len(user.permissions) == 1
    assert user.permissions[0].field_name == "Moran"


def test_repeat_login_reuses_existing_user(db_session):
    claims = validate_token(
        create_token(
            sub="existing-user-789",
            email="existing@oilindia.in",
            name="Existing Eng",
        )
    )
    user1 = get_or_create_user(db_session, claims)
    user2 = get_or_create_user(db_session, claims)
    assert user1.user_id == user2.user_id


def test_admin_role_full_access():
    admin = AuthenticatedUser(
        user_id=uuid.uuid4(),
        email="admin@oilindia.in",
        name="Admin",
        roles=["admin"],
        allowed_fields=None,
    )
    assert admin.can_access_field("AnyField") is True
    assert admin.can_access_field("Moran") is True


def test_field_scoped_user_denied_other_field():
    user = AuthenticatedUser(
        user_id=uuid.uuid4(),
        email="user@oilindia.in",
        name="Scoped User",
        roles=["engineer"],
        allowed_fields=["Digboi"],
    )
    assert user.can_access_field("Digboi") is True
    assert user.can_access_field("Moran") is False


def test_all_fields_grant_allows_any_field():
    user = AuthenticatedUser(
        user_id=uuid.uuid4(),
        email="super@oilindia.in",
        name="Superintendent",
        roles=["superintendent"],
        allowed_fields=None,
    )
    assert user.can_access_field("Moran") is True
    assert user.can_access_field("Digboi") is True


def test_public_routes_bypass_auth():
    app = create_app()
    client = TestClient(app)

    # /health and /ready require no token
    r_health = client.get("/health")
    assert r_health.status_code == 200

    r_ready = client.get("/ready")
    assert r_ready.status_code == 200


def test_protected_route_missing_auth_header_401():
    app = create_app()

    @app.get("/protected-resource")
    def protected(user: AuthenticatedUser = Depends(get_current_user)):
        return {"data": "secret"}

    client = TestClient(app)

    # Missing auth header
    r_no_auth = client.get("/protected-resource")
    assert r_no_auth.status_code == 401
    assert r_no_auth.json()["error_code"] == "unauthorized"

    # Valid token header
    token = create_token(sub="u1", email="u1@oilindia.in", name="U1")
    r_auth = client.get("/protected-resource", headers={"Authorization": f"Bearer {token}"})
    assert r_auth.status_code == 200
    assert r_auth.json() == {"data": "secret"}


def test_missing_permission_403():
    app = create_app()

    @app.post("/admin-only-action", dependencies=[Depends(require_permission("admin", "delete"))])
    def admin_only():
        return {"deleted": True}

    client = TestClient(app)

    # Engineer trying admin action
    token = create_token(sub="eng-1", email="eng@oilindia.in", name="Eng", roles=["engineer"])
    resp = client.post("/admin-only-action", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "forbidden"
