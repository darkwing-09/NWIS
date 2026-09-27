from datetime import datetime, timezone
import uuid
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.main import create_app
from config.errors import ConflictError, ValidationError
from database.models.alerts import Alert
from database.models.base import Base
from database.models.events import Event
from database.models.wells import Well
from database.session import get_db, get_engine
from domain.models.alerts import compute_depth_band, make_dedup_key
from domain.models.correlation import CorrelationResult
from domain.models.risk import RiskAssessmentSchema
from services.alerts import service as alert_service
from services.auth.oidc_client import create_token


@pytest.fixture
def in_memory_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def sample_well(in_memory_db):
    well = Well(
        well_id=uuid.uuid4(),
        name="NHK-045",
        status="active",
        latitude=27.35,
        longitude=95.32,
        field_name="Nahorkatiya",
        basin_name="Assam-Arakan",
    )
    in_memory_db.add(well)
    in_memory_db.commit()
    return well


@pytest.fixture
def sample_event(in_memory_db, sample_well):
    event = Event(
        event_id=uuid.uuid4(),
        well_id=sample_well.well_id,
        depth=2452.0,
        event_type="loss_circulation",
        severity="high",
        description_raw="Severe mud loss encountered in Barail sand.",
        mitigation_taken="Pumped 40 bbls high-viscosity LCM pill (mica + nutshells) and reduced pump rate to 350 gpm.",
        confidence_score=0.95,
        extractor_version="1.0.0",
    )
    in_memory_db.add(event)
    in_memory_db.commit()
    return event


def _make_assessment(well_id: uuid.UUID, risk_level: str, event_ids: list, event_type: str = "loss_circulation"):
    res = CorrelationResult(
        event_type=event_type,
        distinct_well_count=2 if risk_level == "medium" else (3 if risk_level == "high" else 1),
        contributing_wells=["NHK-001", "NHK-002"],
        contributing_well_ids=[uuid.uuid4(), uuid.uuid4()],
        event_ids=event_ids,
    )
    return RiskAssessmentSchema(
        well_id=well_id,
        risk_level=risk_level,
        confidence=0.88,
        method="rule_based_v1",
        contributing_evidence=res,
    )


# --- 14.1 Alert Creation and Suppression Tests ---

def test_creates_alert_on_medium_risk(in_memory_db, sample_well, sample_event):
    assessment = _make_assessment(sample_well.well_id, "medium", [sample_event.event_id])
    alert = alert_service.create_alert_if_needed(
        assessment=assessment,
        well_id=sample_well.well_id,
        current_depth=2450.0,
        db=in_memory_db,
    )
    assert alert is not None
    assert alert.status == "NOTIFIED"
    assert alert.risk_level == "medium"
    assert alert.well_id == sample_well.well_id
    assert alert.dedup_key == make_dedup_key(sample_well.well_id, "2450-2500", "loss_circulation")
    assert alert.recommended_mitigation == sample_event.mitigation_taken


def test_suppresses_duplicate_within_dedup_window(in_memory_db, sample_well, sample_event):
    assessment = _make_assessment(sample_well.well_id, "high", [sample_event.event_id])
    # First call creates alert
    alert1 = alert_service.create_alert_if_needed(
        assessment=assessment,
        well_id=sample_well.well_id,
        current_depth=2450.0,
        db=in_memory_db,
    )
    assert alert1 is not None

    # Second call within same depth band & event type is suppressed
    alert2 = alert_service.create_alert_if_needed(
        assessment=assessment,
        well_id=sample_well.well_id,
        current_depth=2470.0,  # Still in 2450-2500 band
        db=in_memory_db,
    )
    assert alert2 is None


def test_low_risk_creates_nothing(in_memory_db, sample_well, sample_event):
    assessment = _make_assessment(sample_well.well_id, "low", [sample_event.event_id])
    alert = alert_service.create_alert_if_needed(
        assessment=assessment,
        well_id=sample_well.well_id,
        current_depth=2450.0,
        db=in_memory_db,
    )
    assert alert is None


def test_mitigation_retrieved_verbatim_not_generated(in_memory_db, sample_well, sample_event):
    assessment = _make_assessment(sample_well.well_id, "high", [sample_event.event_id])
    alert = alert_service.create_alert_if_needed(
        assessment=assessment,
        well_id=sample_well.well_id,
        current_depth=2450.0,
        db=in_memory_db,
    )
    assert alert is not None
    # Verbatim match requirement: exact substring, exact casing, no summary hallucinations
    assert alert.recommended_mitigation == sample_event.mitigation_taken


# --- 14.2 Verbatim Mitigation Retrieval Tests ---

def test_returns_verbatim_text(in_memory_db, sample_well, sample_event):
    # Add a second event further away in depth
    event_far = Event(
        event_id=uuid.uuid4(),
        well_id=sample_well.well_id,
        depth=2550.0,
        event_type="loss_circulation",
        severity="medium",
        description_raw="Another loss",
        mitigation_taken="Added LCM pill at 2550m.",
        confidence_score=0.9,
    )
    in_memory_db.add(event_far)
    in_memory_db.commit()

    # Query targeting 2450m should pick sample_event (at 2452m), not event_far (at 2550m)
    retrieved = alert_service.retrieve_mitigation(
        event_ids=[sample_event.event_id, event_far.event_id],
        target_depth=2450.0,
        db=in_memory_db,
    )
    assert retrieved == sample_event.mitigation_taken


def test_returns_none_if_no_mitigation_recorded(in_memory_db, sample_well):
    event_empty = Event(
        event_id=uuid.uuid4(),
        well_id=sample_well.well_id,
        depth=2450.0,
        event_type="kick",
        severity="high",
        description_raw="Gas kick without recorded remedy",
        mitigation_taken=None,
        confidence_score=0.9,
    )
    in_memory_db.add(event_empty)
    in_memory_db.commit()

    retrieved = alert_service.retrieve_mitigation(
        event_ids=[event_empty.event_id],
        target_depth=2450.0,
        db=in_memory_db,
    )
    assert retrieved is None


# --- 14.3 Alert Lifecycle State Machine Tests ---

def test_acknowledge_transitions_status(in_memory_db, sample_well, sample_event):
    assessment = _make_assessment(sample_well.well_id, "high", [sample_event.event_id])
    alert = alert_service.create_alert_if_needed(
        assessment=assessment,
        well_id=sample_well.well_id,
        current_depth=2450.0,
        db=in_memory_db,
    )
    assert alert.status == "NOTIFIED"

    user_id = uuid.uuid4()
    ack_alert = alert_service.acknowledge(alert.alert_id, user_id=user_id, db=in_memory_db)
    assert ack_alert.status == "ACKNOWLEDGED"
    assert ack_alert.acknowledged_by == user_id
    assert ack_alert.acknowledged_at is not None


def test_cannot_acknowledge_already_resolved_alert(in_memory_db, sample_well, sample_event):
    assessment = _make_assessment(sample_well.well_id, "high", [sample_event.event_id])
    alert = alert_service.create_alert_if_needed(
        assessment=assessment,
        well_id=sample_well.well_id,
        current_depth=2450.0,
        db=in_memory_db,
    )
    user_id = uuid.uuid4()
    alert_service.acknowledge(alert.alert_id, user_id=user_id, db=in_memory_db)
    alert_service.resolve(alert.alert_id, user_id=user_id, db=in_memory_db)

    # Trying to acknowledge an already resolved alert must raise ConflictError
    with pytest.raises(ConflictError):
        alert_service.acknowledge(alert.alert_id, user_id=user_id, db=in_memory_db)


def test_resolve_requires_prior_acknowledgement(in_memory_db, sample_well, sample_event):
    assessment = _make_assessment(sample_well.well_id, "high", [sample_event.event_id])
    alert = alert_service.create_alert_if_needed(
        assessment=assessment,
        well_id=sample_well.well_id,
        current_depth=2450.0,
        db=in_memory_db,
    )
    user_id = uuid.uuid4()

    # Calling resolve directly on NOTIFIED alert must fail
    with pytest.raises(ValidationError):
        alert_service.resolve(alert.alert_id, user_id=user_id, db=in_memory_db)

    # Once acknowledged, resolve succeeds
    alert_service.acknowledge(alert.alert_id, user_id=user_id, db=in_memory_db)
    resolved = alert_service.resolve(alert.alert_id, user_id=user_id, db=in_memory_db)
    assert resolved.status == "RESOLVED"
    assert resolved.resolved_at is not None


# --- 14.4 API Router Tests ---

def test_alert_api_endpoints(in_memory_db, sample_well, sample_event):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: in_memory_db

    assessment = _make_assessment(sample_well.well_id, "high", [sample_event.event_id])
    alert = alert_service.create_alert_if_needed(
        assessment=assessment,
        well_id=sample_well.well_id,
        current_depth=2450.0,
        db=in_memory_db,
    )

    user_id = uuid.uuid4()
    token = create_token(
        user_id=user_id,
        email="engineer@oilindia.in",
        roles=["drilling_engineer", "admin"],
    )
    headers = {"Authorization": f"Bearer {token}"}

    client = TestClient(app)

    # GET /alerts
    res = client.get("/alerts", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert len(data) >= 1
    assert data[0]["alert_id"] == str(alert.alert_id)

    # GET /alerts/{id}
    res_single = client.get(f"/alerts/{alert.alert_id}", headers=headers)
    assert res_single.status_code == 200
    assert res_single.json()["status"] == "NOTIFIED"

    # POST /alerts/{id}/acknowledge
    res_ack = client.post(f"/alerts/{alert.alert_id}/acknowledge", headers=headers)
    assert res_ack.status_code == 200
    assert res_ack.json()["status"] == "ACKNOWLEDGED"

    # POST /alerts/{id}/resolve
    res_resolve = client.post(f"/alerts/{alert.alert_id}/resolve", headers=headers)
    assert res_resolve.status_code == 200
    assert res_resolve.json()["status"] == "RESOLVED"
