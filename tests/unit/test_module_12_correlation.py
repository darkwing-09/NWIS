from datetime import datetime, timezone
import uuid
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from config.errors import WellNotFoundError
from database.models.base import Base
from database.models.events import Event
from database.models.wells import Well, WellFormationInterval
from domain.models.correlation import CorrelationResult, EventGroup
from domain.models.enums import EventType
from services.correlation.engine import (
    calculate_pattern_strength,
    find_events_in_depth_window,
    find_matching_formation_intervals,
    find_nearby_wells,
    group_events_by_pattern,
    normalize_event_types,
    run_correlation,
)
from tests.fixtures.synthetic_wells import generate_synthetic_dataset


@pytest.fixture
def in_memory_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionMaker = sessionmaker(bind=engine)
    session = SessionMaker()
    yield session
    session.close()


# -------------------------------------------------------------
# 12.1 Spatial Offset Well Filtering
# -------------------------------------------------------------

def test_finds_wells_within_radius(in_memory_db):
    target = Well(well_id=uuid.uuid4(), name="TARGET", status="active", latitude=27.35, longitude=95.32)
    nearby = Well(well_id=uuid.uuid4(), name="NEARBY", status="active", latitude=27.36, longitude=95.33)
    in_memory_db.add_all([target, nearby])
    in_memory_db.commit()

    results = find_nearby_wells(target.well_id, radius_km=5.0, db=in_memory_db)
    names = [w.name for w in results]
    assert "NEARBY" in names


def test_excludes_wells_outside_radius(in_memory_db):
    target = Well(well_id=uuid.uuid4(), name="TARGET", status="active", latitude=27.35, longitude=95.32)
    far_away = Well(well_id=uuid.uuid4(), name="FAR_AWAY", status="active", latitude=28.50, longitude=96.50)
    in_memory_db.add_all([target, far_away])
    in_memory_db.commit()

    results = find_nearby_wells(target.well_id, radius_km=10.0, db=in_memory_db)
    assert len(results) == 0


def test_excludes_abandoned_wells(in_memory_db):
    target = Well(well_id=uuid.uuid4(), name="TARGET", status="active", latitude=27.35, longitude=95.32)
    abandoned = Well(well_id=uuid.uuid4(), name="ABANDONED", status="abandoned", latitude=27.351, longitude=95.321)
    in_memory_db.add_all([target, abandoned])
    in_memory_db.commit()

    results = find_nearby_wells(target.well_id, radius_km=5.0, db=in_memory_db)
    assert len(results) == 0


# -------------------------------------------------------------
# 12.2 Formation Interval Matching
# -------------------------------------------------------------

def test_matches_exact_formation_name(in_memory_db):
    w1 = uuid.uuid4()
    w2 = uuid.uuid4()
    int1 = WellFormationInterval(interval_id=uuid.uuid4(), well_id=w1, formation_name="Barail", top_depth=2000.0, bottom_depth=2500.0)
    int2 = WellFormationInterval(interval_id=uuid.uuid4(), well_id=w2, formation_name="Tipam", top_depth=1200.0, bottom_depth=1800.0)
    in_memory_db.add_all([int1, int2])
    in_memory_db.commit()

    matched = find_matching_formation_intervals([w1, w2], formation="Barail", db=in_memory_db)
    assert len(matched) == 1
    assert matched[0].well_id == w1
    assert matched[0].formation_name == "Barail"


def test_no_match_returns_empty(in_memory_db):
    w1 = uuid.uuid4()
    int1 = WellFormationInterval(interval_id=uuid.uuid4(), well_id=w1, formation_name="Tipam", top_depth=1000.0, bottom_depth=1500.0)
    in_memory_db.add(int1)
    in_memory_db.commit()

    matched = find_matching_formation_intervals([w1], formation="NonExistentFm", db=in_memory_db)
    assert matched == []


# -------------------------------------------------------------
# 12.3 Depth Window Event Matching
# -------------------------------------------------------------

def test_events_within_band_included(in_memory_db):
    w1 = uuid.uuid4()
    ev = Event(
        event_id=uuid.uuid4(),
        well_id=w1,
        depth=2450.0,
        event_type="kick",
        description_raw="Kick at 2450m",
        extractor_version="1.0.0",
    )
    in_memory_db.add(ev)
    in_memory_db.commit()

    # Target depth 2400m, band 100m -> window [2300m, 2500m]
    events = find_events_in_depth_window([w1], depth=2400.0, band_m=100.0, db=in_memory_db)
    assert len(events) == 1
    assert events[0].depth == 2450.0


def test_events_outside_band_excluded(in_memory_db):
    w1 = uuid.uuid4()
    ev = Event(
        event_id=uuid.uuid4(),
        well_id=w1,
        depth=2650.0,
        event_type="kick",
        description_raw="Kick at 2650m",
        extractor_version="1.0.0",
    )
    in_memory_db.add(ev)
    in_memory_db.commit()

    # Target depth 2400m, band 100m -> window [2300m, 2500m]
    events = find_events_in_depth_window([w1], depth=2400.0, band_m=100.0, db=in_memory_db)
    assert len(events) == 0


# -------------------------------------------------------------
# 12.4 Event Normalization Gate
# -------------------------------------------------------------

def test_canonical_types_pass_through():
    ev1 = Event(event_id=uuid.uuid4(), well_id=uuid.uuid4(), depth=1000.0, event_type="kick", description_raw="")
    ev2 = Event(event_id=uuid.uuid4(), well_id=uuid.uuid4(), depth=1100.0, event_type="lost circulation", description_raw="")

    normalized = normalize_event_types([ev1, ev2])
    assert len(normalized) == 2
    assert normalized[0].event_type == "kick"
    assert normalized[1].event_type == "loss_circulation"


def test_unknown_type_logged_and_excluded():
    ev1 = Event(event_id=uuid.uuid4(), well_id=uuid.uuid4(), depth=1000.0, event_type="kick", description_raw="")
    ev_unknown = Event(event_id=uuid.uuid4(), well_id=uuid.uuid4(), depth=1000.0, event_type="unrecognized_extraneous_noise", description_raw="")

    normalized = normalize_event_types([ev1, ev_unknown])
    assert len(normalized) == 1
    assert normalized[0].event_type == "kick"


# -------------------------------------------------------------
# 12.5 Grouping by Pattern
# -------------------------------------------------------------

def test_groups_by_event_type():
    w1, w2 = uuid.uuid4(), uuid.uuid4()
    e1 = Event(event_id=uuid.uuid4(), well_id=w1, depth=1000.0, event_type="kick", description_raw="")
    e2 = Event(event_id=uuid.uuid4(), well_id=w2, depth=1050.0, event_type="loss_circulation", description_raw="")

    groups = group_events_by_pattern([e1, e2])
    types = {g.event_type for g in groups}
    assert types == {"kick", "loss_circulation"}


def test_counts_distinct_wells_not_events():
    w1, w2 = uuid.uuid4(), uuid.uuid4()
    e1 = Event(event_id=uuid.uuid4(), well_id=w1, depth=1000.0, event_type="kick", description_raw="")
    e2 = Event(event_id=uuid.uuid4(), well_id=w2, depth=1010.0, event_type="kick", description_raw="")

    groups = group_events_by_pattern([e1, e2])
    assert len(groups) == 1
    assert groups[0].event_type == "kick"
    assert groups[0].distinct_well_count == 2


def test_multiple_events_same_well_counted_once():
    w1 = uuid.uuid4()
    e1 = Event(event_id=uuid.uuid4(), well_id=w1, depth=1000.0, event_type="kick", description_raw="")
    e2 = Event(event_id=uuid.uuid4(), well_id=w1, depth=1020.0, event_type="kick", description_raw="")
    e3 = Event(event_id=uuid.uuid4(), well_id=w1, depth=1040.0, event_type="kick", description_raw="")

    groups = group_events_by_pattern([e1, e2, e3])
    assert len(groups) == 1
    assert groups[0].distinct_well_count == 1
    assert len(groups[0].event_ids) == 3


# -------------------------------------------------------------
# 12.6 Strength Calculation & Output Schema
# -------------------------------------------------------------

def test_output_schema_complete(in_memory_db):
    w1 = Well(well_id=uuid.uuid4(), name="WELL-A", status="active", latitude=27.0, longitude=95.0)
    in_memory_db.add(w1)
    in_memory_db.commit()

    group = EventGroup(
        event_type="kick",
        distinct_well_count=1,
        well_ids={w1.well_id},
        event_ids=[uuid.uuid4()],
    )
    results = calculate_pattern_strength([group], db=in_memory_db)
    assert len(results) == 1
    assert results[0].event_type == "kick"
    assert results[0].distinct_well_count == 1
    assert results[0].contributing_wells == ["WELL-A"]
    assert results[0].event_ids == group.event_ids


def test_empty_input_returns_empty_list():
    assert calculate_pattern_strength([]) == []


# -------------------------------------------------------------
# 12.7 End-to-End Correlation (Single Most Important Test)
# -------------------------------------------------------------

def test_end_to_end_correlation_with_planted_overlap(in_memory_db):
    """
    Runs the correlation engine against the synthetic fixture generator and asserts
    that the deliberately-planted cross-well pattern surfaces accurately.
    """
    fixture = generate_synthetic_dataset(num_wells=8, planted_overlaps=3)
    fixture.populate_db(in_memory_db)

    # Run correlation on origin well at the planted depth and formation
    results = run_correlation(
        well_id=fixture.target_well_id,
        depth=fixture.planted_depth,
        formation=fixture.planted_formation,
        radius_km=15.0,
        depth_band_m=100.0,
        db=in_memory_db,
    )

    assert len(results) >= 1
    top_result = results[0]

    # Verify the planted event pattern surfaced with the exact distinct count
    assert top_result.event_type == fixture.planted_event_type
    assert top_result.distinct_well_count == fixture.planted_distinct_wells
    assert len(top_result.contributing_wells) == 3
    for name in top_result.contributing_wells:
        assert "SYN-NHK-OFFSET" in name


def test_no_nearby_wells_returns_empty(in_memory_db):
    isolated_well = Well(
        well_id=uuid.uuid4(),
        name="ISOLATED-WELL",
        status="active",
        latitude=10.0,
        longitude=10.0,
    )
    in_memory_db.add(isolated_well)
    in_memory_db.commit()

    results = run_correlation(
        well_id=isolated_well.well_id,
        depth=2000.0,
        formation="Barail",
        radius_km=5.0,
        db=in_memory_db,
    )
    assert results == []


def test_no_formation_match_returns_empty(in_memory_db):
    target = Well(well_id=uuid.uuid4(), name="TARGET", status="active", latitude=27.35, longitude=95.32)
    nearby = Well(well_id=uuid.uuid4(), name="NEARBY", status="active", latitude=27.36, longitude=95.33)
    in_memory_db.add_all([target, nearby])
    # Give nearby well a completely different formation
    diff_int = WellFormationInterval(
        interval_id=uuid.uuid4(),
        well_id=nearby.well_id,
        formation_name="Disang",
        top_depth=3000.0,
        bottom_depth=3500.0,
    )
    in_memory_db.add(diff_int)
    in_memory_db.commit()

    # Search for "Barail", which nearby well didn't penetrate
    results = run_correlation(
        well_id=target.well_id,
        depth=2000.0,
        formation="Barail",
        radius_km=5.0,
        db=in_memory_db,
    )
    assert results == []
