import uuid
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models.base import Base
from database.models.wells import Well, WellFormationInterval
from domain.models.wells import (
    AuthenticatedUser,
    FormationIntervalCreate,
    WellCreate,
)
from services.wells.service import (
    create_formation_interval,
    create_well,
    get_well,
    list_formations,
    list_wells,
    update_well_status,
)
from database.repositories.wells import fuzzy_match_well_name, query_nearby
from config.errors import AuthorizationError, NotFoundError, ValidationError


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    yield session
    session.close()


@pytest.fixture
def admin_user():
    return AuthenticatedUser(
        user_id=uuid.uuid4(),
        email="admin@oilindia.in",
        name="Admin User",
        roles=["admin"],
        allowed_fields=None,
    )


@pytest.fixture
def field_user():
    return AuthenticatedUser(
        user_id=uuid.uuid4(),
        email="engineer@oilindia.in",
        name="Field Engineer",
        roles=["engineer"],
        allowed_fields=["Digboi", "Naharkatiya"],
    )


def test_create_well_persists_geom_correctly(db_session, admin_user):
    data = WellCreate(
        name="OIL-NH-101",
        latitude=27.48,
        longitude=95.35,
        field_name="Digboi",
        basin_name="Assam-Arakan",
    )
    well = create_well(data, admin_user, db_session)
    assert well.well_id is not None
    assert well.name == "OIL-NH-101"
    assert well.latitude == 27.48
    assert well.longitude == 95.35
    assert well.status == "active"


def test_get_well_denies_out_of_scope_field(db_session, admin_user, field_user):
    # Create well in Moran field (out of scope for field_user)
    data = WellCreate(
        name="OIL-MR-005",
        latitude=27.18,
        longitude=94.92,
        field_name="Moran",
    )
    well = create_well(data, admin_user, db_session)

    # Admin can access
    assert get_well(well.well_id, admin_user, db_session).name == "OIL-MR-005"

    # Field user denied
    with pytest.raises(AuthorizationError):
        get_well(well.well_id, field_user, db_session)


def test_list_wells_filters_by_field_and_status(db_session, admin_user, field_user):
    w1 = create_well(
        WellCreate(name="W1", latitude=27.5, longitude=95.3, field_name="Digboi"),
        admin_user,
        db_session,
    )
    w2 = create_well(
        WellCreate(name="W2", latitude=27.4, longitude=95.4, field_name="Naharkatiya"),
        admin_user,
        db_session,
    )
    w3 = create_well(
        WellCreate(name="W3", latitude=27.1, longitude=94.9, field_name="Moran"),
        admin_user,
        db_session,
    )

    # field_user should only see Digboi and Naharkatiya
    wells = list_wells(field_user, db=db_session)
    names = [w.name for w in wells]
    assert "W1" in names
    assert "W2" in names
    assert "W3" not in names


def test_status_transition_rejects_invalid_sequence(db_session, admin_user):
    w = create_well(
        WellCreate(name="StatusWell", latitude=27.5, longitude=95.3, field_name="Digboi"),
        admin_user,
        db_session,
    )
    # Skipping: active -> abandoned directly is invalid
    with pytest.raises(ValidationError):
        update_well_status(w.well_id, "abandoned", admin_user, admin_override=False, db=db_session)

    # Valid: active -> completed
    w = update_well_status(w.well_id, "completed", admin_user, admin_override=False, db=db_session)
    assert w.status == "completed"

    # Valid: completed -> abandoned
    w = update_well_status(w.well_id, "abandoned", admin_user, admin_override=False, db=db_session)
    assert w.status == "abandoned"

    # Reverse: abandoned -> active without override is invalid
    with pytest.raises(ValidationError):
        update_well_status(w.well_id, "active", admin_user, admin_override=False, db=db_session)

    # Reverse with admin_override=True succeeds
    w = update_well_status(w.well_id, "active", admin_user, admin_override=True, db=db_session)
    assert w.status == "active"


def test_fuzzy_match_finds_close_name(db_session, admin_user):
    create_well(
        WellCreate(name="Naharkatiya-45B", latitude=27.5, longitude=95.3, field_name="Naharkatiya"),
        admin_user,
        db_session,
    )
    # Slightly misspelled query
    matched = fuzzy_match_well_name(db_session, "Naharkatiya-45", threshold=0.8)
    assert matched is not None
    assert matched.name == "Naharkatiya-45B"


def test_fuzzy_match_below_threshold_returns_none(db_session, admin_user):
    create_well(
        WellCreate(name="Naharkatiya-45B", latitude=27.5, longitude=95.3, field_name="Naharkatiya"),
        admin_user,
        db_session,
    )
    matched = fuzzy_match_well_name(db_session, "CompletelyDifferentWell", threshold=0.8)
    assert matched is None


def test_query_nearby_finds_wells_within_radius(db_session, admin_user):
    # Origin: Digboi center (27.38, 95.63)
    origin = create_well(
        WellCreate(name="OriginWell", latitude=27.380, longitude=95.630, field_name="Digboi"),
        admin_user,
        db_session,
    )
    # Close well ~2 km away (27.39, 95.64)
    close_well = create_well(
        WellCreate(name="CloseWell", latitude=27.390, longitude=95.640, field_name="Digboi"),
        admin_user,
        db_session,
    )
    # Far well ~150 km away (26.50, 94.00)
    far_well = create_well(
        WellCreate(name="FarWell", latitude=26.500, longitude=94.000, field_name="Other"),
        admin_user,
        db_session,
    )

    nearby = query_nearby(db_session, origin.well_id, radius_km=10.0)
    assert len(nearby) == 1
    assert nearby[0].name == "CloseWell"
    assert nearby[0].distance_km is not None
    assert nearby[0].distance_km < 10.0


def test_formation_interval_top_less_than_bottom_validation(db_session, admin_user):
    well = create_well(
        WellCreate(name="IntervalWell", latitude=27.5, longitude=95.3, field_name="Digboi"),
        admin_user,
        db_session,
    )
    # Invalid: top >= bottom
    with pytest.raises(ValidationError):
        create_formation_interval(
            well.well_id,
            FormationIntervalCreate(formation_name="Barail", top_depth=2500.0, bottom_depth=2400.0),
            admin_user,
            db_session,
        )

    # Valid: top < bottom
    interval = create_formation_interval(
        well.well_id,
        FormationIntervalCreate(formation_name="Barail", top_depth=2300.0, bottom_depth=2600.0),
        admin_user,
        db_session,
    )
    assert interval.interval_id is not None
    assert interval.formation_name == "Barail"

    formations = list_formations(well.well_id, admin_user, db_session)
    assert len(formations) == 1
    assert formations[0].formation_name == "Barail"
