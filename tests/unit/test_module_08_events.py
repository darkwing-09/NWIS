from datetime import datetime, timezone
import uuid
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from apps.api.main import create_app
from database.models.base import Base
from database.models.events import Event, IncidentEvent
from database.models.wells import Well
from database.repositories.events import (
    create_event,
    find_duplicate_candidates,
    link_duplicate_group,
    list_events,
    query_by_wells_and_depth_range,
    supersede_event,
)
from database.session import get_db
from domain.models.auth import AuthenticatedUser
from domain.models.enums import EventType
from domain.models.events import EventCreate, IncidentCreate, Provenance
from services.auth.oidc_client import create_token


from sqlalchemy.pool import StaticPool


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
        name="NHK-088",
        status="active",
        latitude=27.35,
        longitude=95.32,
        field_name="Nahorkatiya",
        basin_name="Assam-Arakan",
    )
    in_memory_db.add(well)
    in_memory_db.commit()
    return well


def test_create_event_stamps_provenance(in_memory_db, sample_well):
    doc_id = uuid.uuid4()
    prov = Provenance(
        extractor_version="1.0.0",
        model_version="nwis-extractor-v1",
        source_document_id=doc_id,
    )
    data = EventCreate(
        well_id=sample_well.well_id,
        depth=2450.0,
        event_type=EventType.KICK,
        severity="high",
        description_raw="15 bbl kick while drilling",
        mitigation_taken="Shut in and circulated kill mud",
        confidence_score=0.95,
    )
    event = create_event(in_memory_db, data, prov)

    assert event.event_id is not None
    assert event.well_id == sample_well.well_id
    assert event.depth == 2450.0
    assert event.event_type == "kick"
    assert event.extractor_version == "1.0.0"
    assert event.model_version == "nwis-extractor-v1"
    assert event.source_document_id == doc_id
    assert event.valid_from is not None


def test_duplicate_candidates_found_within_band(in_memory_db, sample_well):
    prov = Provenance(source_document_id=uuid.uuid4())

    ev1 = create_event(
        in_memory_db,
        EventCreate(
            well_id=sample_well.well_id,
            depth=3000.0,
            event_type="loss_circulation",
            description_raw="Severe mud loss",
        ),
        prov,
    )

    # Event within ±5m: depth=3004.0, same well, same event_type
    candidates = find_duplicate_candidates(
        in_memory_db,
        well_id=sample_well.well_id,
        depth=3004.0,
        event_type="loss_circulation",
        band_m=5.0,
    )
    assert len(candidates) == 1
    assert candidates[0].event_id == ev1.event_id

    # Create second event which automatically detects ev1 as candidate and assigns group
    ev2 = create_event(
        in_memory_db,
        EventCreate(
            well_id=sample_well.well_id,
            depth=3003.5,
            event_type="loss_circulation",
            description_raw="Mud loss into formation",
        ),
        prov,
    )

    in_memory_db.refresh(ev1)
    assert ev1.duplicate_group_id is not None
    assert ev2.duplicate_group_id == ev1.duplicate_group_id


def test_duplicate_candidates_excludes_outside_band(in_memory_db, sample_well):
    prov = Provenance()
    create_event(
        in_memory_db,
        EventCreate(
            well_id=sample_well.well_id,
            depth=3000.0,
            event_type="kick",
            description_raw="Gas kick",
        ),
        prov,
    )

    # Outside band (3006m is > 5m from 3000m)
    candidates_depth = find_duplicate_candidates(
        in_memory_db,
        well_id=sample_well.well_id,
        depth=3006.0,
        event_type="kick",
        band_m=5.0,
    )
    assert len(candidates_depth) == 0

    # Different event_type
    candidates_type = find_duplicate_candidates(
        in_memory_db,
        well_id=sample_well.well_id,
        depth=3000.0,
        event_type="stuck_pipe",
        band_m=5.0,
    )
    assert len(candidates_type) == 0

    # Different well
    other_well_id = uuid.uuid4()
    candidates_well = find_duplicate_candidates(
        in_memory_db,
        well_id=other_well_id,
        depth=3000.0,
        event_type="kick",
        band_m=5.0,
    )
    assert len(candidates_well) == 0


def test_supersede_never_deletes_original(in_memory_db, sample_well):
    prov = Provenance()
    original_event = create_event(
        in_memory_db,
        EventCreate(
            well_id=sample_well.well_id,
            depth=1500.0,
            event_type="tight_hole",
            description_raw="Original event: tight hole reported",
        ),
        prov,
    )
    original_id = original_event.event_id

    # Supersede with corrected amendment
    amended_data = EventCreate(
        well_id=sample_well.well_id,
        depth=1500.0,
        event_type="stuck_pipe",  # corrected from tight hole to stuck pipe
        description_raw="Amended event: pipe actually stuck at 1500m",
    )
    new_event = supersede_event(in_memory_db, original_id, amended_data, prov)

    # Check original event STILL exists in database (non-negotiable Part 8 audit rule)
    original_reloaded = in_memory_db.get(Event, original_id)
    assert original_reloaded is not None
    assert original_reloaded.event_id == original_id
    assert original_reloaded.event_type == "tight_hole"
    assert original_reloaded.superseded_by_event_id == new_event.event_id
    assert original_reloaded.valid_to is not None


def test_supersede_links_correctly(in_memory_db, sample_well):
    prov = Provenance()
    ev1 = create_event(
        in_memory_db,
        EventCreate(
            well_id=sample_well.well_id,
            depth=2200.0,
            event_type="equipment_failure",
            description_raw="MWD tool failure",
        ),
        prov,
    )

    ev2 = supersede_event(
        in_memory_db,
        ev1.event_id,
        EventCreate(
            well_id=sample_well.well_id,
            depth=2200.0,
            event_type="equipment_failure",
            description_raw="MWD tool failure due to telemetry pulser jam",
        ),
        prov,
    )

    assert ev1.superseded_by_event_id == ev2.event_id
    assert ev1.valid_to == ev2.valid_from


def test_depth_range_query_matches_correlation_engine_expectations(in_memory_db, sample_well):
    prov = Provenance()
    # Create 3 events at different depths
    ev1 = create_event(
        in_memory_db,
        EventCreate(well_id=sample_well.well_id, depth=1900.0, event_type="kick", description_raw="shallow kick"),
        prov,
    )
    ev2 = create_event(
        in_memory_db,
        EventCreate(well_id=sample_well.well_id, depth=2000.0, event_type="kick", description_raw="target kick"),
        prov,
    )
    ev3 = create_event(
        in_memory_db,
        EventCreate(well_id=sample_well.well_id, depth=2150.0, event_type="kick", description_raw="deep kick"),
        prov,
    )

    # Query +/- 100m band around 2000m (depth_min=1900, depth_max=2100)
    matched = query_by_wells_and_depth_range(
        in_memory_db,
        well_ids=[sample_well.well_id],
        depth_min=1900.0,
        depth_max=2100.0,
    )
    matched_ids = [m.event_id for m in matched]
    assert ev1.event_id in matched_ids
    assert ev2.event_id in matched_ids
    assert ev3.event_id not in matched_ids  # 2150m is outside range


def test_list_events_filtering_and_pagination(in_memory_db, sample_well):
    prov = Provenance()
    for i in range(10):
        create_event(
            in_memory_db,
            EventCreate(
                well_id=sample_well.well_id,
                depth=1000.0 + i * 50,
                event_type="kick" if i % 2 == 0 else "loss_circulation",
                description_raw=f"Event {i}",
            ),
            prov,
        )

    # Filter by event_type="kick"
    kicks, total_kicks = list_events(in_memory_db, sample_well.well_id, event_type="kick")
    assert total_kicks == 5
    assert len(kicks) == 5

    # Filter with pagination: page_size=2
    page1, total = list_events(in_memory_db, sample_well.well_id, page=1, page_size=2)
    assert total == 10
    assert len(page1) == 2


def test_get_well_events_api_endpoint(in_memory_db, sample_well):
    prov = Provenance()
    create_event(
        in_memory_db,
        EventCreate(
            well_id=sample_well.well_id,
            depth=2500.0,
            event_type="kick",
            description_raw="API event test",
        ),
        prov,
        incident_data=IncidentCreate(
            cause="Overpressured shale",
            outcome="Circulated out",
            auto_title="Kick at 2500m",
        ),
    )

    app = create_app()
    app.dependency_overrides[get_db] = lambda: in_memory_db

    token = create_token(
        sub="test-user-id",
        email="test@oilindia.in",
        name="Test User",
        roles=["viewer"],
        allowed_fields=["Nahorkatiya"],
    )

    client = TestClient(app)
    response = client.get(
        f"/wells/{sample_well.well_id}/events",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["event_type"] == "kick"
    assert data["items"][0]["cause"] == "Overpressured shale"
    assert data["items"][0]["auto_title"] == "Kick at 2500m"
