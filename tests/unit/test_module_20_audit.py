import uuid
from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from database.models.base import Base
from database.models.audit import AuditLog
from database.models.wells import Well
from database.models.auth import User, Role, Permission
from services.audit.service import log_action, query_logs
from apps.api.main import create_app
from database.session import get_session
from sqlalchemy.pool import StaticPool
from services.auth.oidc_client import create_token


@pytest.fixture
def audit_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session, engine


def test_log_action_persists(audit_db):
    session, engine = audit_db
    user_id = uuid.uuid4()
    doc_id = uuid.uuid4()

    log_action(
        user_id=user_id,
        action="document_viewed",
        resource_type="document",
        resource_id=doc_id,
        detail={"client": "test"},
        engine=engine,
    )

    with Session(engine) as verify_session:
        logs = verify_session.query(AuditLog).all()
        assert len(logs) == 1
        assert logs[0].action == "document_viewed"
        assert logs[0].resource_type == "document"
        assert logs[0].resource_id == doc_id
        assert logs[0].user_id == user_id
        assert logs[0].detail == {"client": "test"}


def test_log_survives_caller_rollback(audit_db):
    session, engine = audit_db
    well_id = uuid.uuid4()

    # Simulate caller attempting an operation in a transaction that fails and rolls back
    try:
        caller_well = Well(
            well_id=well_id,
            name="TEMP-WELL",
            field_name="DIBRUGARH",
            latitude=27.4,
            longitude=94.9,
            spud_date=datetime.now(timezone.utc).date(),
            status="active",
        )
        session.add(caller_well)
        raise RuntimeError("Caller transaction failure")
    except RuntimeError:
        session.rollback()
        # Independent audit logging of the failure/attempt
        log_action(
            user_id=None,
            action="well_attempted",
            resource_type="well",
            resource_id=well_id,
            detail={"attempt": 1},
            engine=engine,
        )

    # Verify caller_well was rolled back
    assert session.query(Well).filter_by(well_id=well_id).first() is None

    # Verify the independent audit log survived and is present
    with Session(engine) as verify_session:
        audit_entry = verify_session.query(AuditLog).filter_by(resource_id=well_id).first()
        assert audit_entry is not None
        assert audit_entry.action == "well_attempted"


def test_audit_append_only_no_update_grant(audit_db):
    session, engine = audit_db
    doc_id = uuid.uuid4()

    log_action(
        user_id=None,
        action="initial_entry",
        resource_type="document",
        resource_id=doc_id,
        engine=engine,
    )

    with Session(engine) as edit_session:
        log_entry = edit_session.query(AuditLog).first()
        log_entry.action = "tampered_action"

        with pytest.raises(PermissionError, match="append-only: UPDATE is strictly prohibited"):
            edit_session.flush()

        edit_session.rollback()

        log_entry = edit_session.query(AuditLog).first()
        edit_session.delete(log_entry)
        with pytest.raises(PermissionError, match="append-only: DELETE is strictly prohibited"):
            edit_session.flush()


def test_query_filters_by_resource(audit_db):
    session, engine = audit_db
    res_a = uuid.uuid4()
    res_b = uuid.uuid4()

    log_action(None, "view", "well", res_a, engine=engine)
    log_action(None, "edit", "well", res_a, engine=engine)
    log_action(None, "view", "document", res_b, engine=engine)

    with Session(engine) as query_session:
        res = query_logs(query_session, resource_type="well")
        assert res.total == 2
        assert all(item.resource_type == "well" for item in res.items)

        res_specific = query_logs(query_session, resource_id=res_a)
        assert res_specific.total == 2

        res_other = query_logs(query_session, resource_type="document")
        assert res_other.total == 1
        assert res_other.items[0].resource_id == res_b


def test_query_date_range(audit_db):
    session, engine = audit_db
    now = datetime.now(timezone.utc)
    t1 = now - timedelta(days=5)
    t2 = now - timedelta(days=2)
    t3 = now

    with Session(engine) as s:
        s.add(AuditLog(log_id=uuid.uuid4(), action="a1", resource_type="res", resource_id=uuid.uuid4(), timestamp=t1))
        s.add(AuditLog(log_id=uuid.uuid4(), action="a2", resource_type="res", resource_id=uuid.uuid4(), timestamp=t2))
        s.add(AuditLog(log_id=uuid.uuid4(), action="a3", resource_type="res", resource_id=uuid.uuid4(), timestamp=t3))
        s.commit()

    with Session(engine) as query_session:
        # Date range between t1 + 1 day and t3 - 1 day (should only find t2)
        res = query_logs(
            query_session,
            date_from=now - timedelta(days=3),
            date_to=now - timedelta(days=1),
        )
        assert res.total == 1
        assert res.items[0].action == "a2"


def test_pagination(audit_db):
    session, engine = audit_db
    res_id = uuid.uuid4()

    for i in range(12):
        log_action(None, f"action_{i}", "resource", res_id, engine=engine)

    with Session(engine) as query_session:
        page_1 = query_logs(query_session, page=1, page_size=5)
        assert page_1.total == 12
        assert len(page_1.items) == 5
        assert page_1.page == 1

        page_2 = query_logs(query_session, page=2, page_size=5)
        assert len(page_2.items) == 5
        assert page_2.page == 2

        page_3 = query_logs(query_session, page=3, page_size=5)
        assert len(page_3.items) == 2
        assert page_3.page == 3


def test_audit_api_endpoint(audit_db):
    _, engine = audit_db
    app = create_app()

    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    # 1. Unauthenticated request -> 401
    resp = client.get("/audit/logs")
    assert resp.status_code == 401

    # 2. Authenticated user without audit:read -> 403
    user_token = create_token(sub="user-regular", email="regular@oilindia.in", name="Regular User", roles=["geologist"])
    resp = client.get("/audit/logs", headers={"Authorization": f"Bearer {user_token}"})
    assert resp.status_code == 403

    # Seed an audit log
    log_action(None, "alert_resolved", "alert", uuid.uuid4(), {"note": "safe"}, engine=engine)

    # 3. Admin user -> 200 with data
    admin_token = create_token(sub="admin-1", email="admin@oilindia.in", name="Admin", roles=["admin"])
    resp = client.get("/audit/logs", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert data["items"][0]["action"] == "alert_resolved"
