import uuid
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models.base import Base
from domain.models.wells import WellCreate
from database.repositories import wells as well_repo
from services.wells.geospatial import (
    calculate_distance,
    find_nearby_wells,
    maintain_spatial_index,
)
from config.errors import NotFoundError


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    yield session
    session.close()


def test_calculate_distance_accuracy_against_known_points(db_session):
    # Point A: Digboi Oilfield (27.388, 95.630)
    # Point B: Naharkatiya Field (27.275, 95.345)
    # Ground truth distance ~31.0 km (~31,000 meters)
    well_a = well_repo.create_well(
        db_session,
        WellCreate(name="Digboi-01", latitude=27.388, longitude=95.630, field_name="Digboi"),
    )
    well_b = well_repo.create_well(
        db_session,
        WellCreate(name="Naharkatiya-01", latitude=27.275, longitude=95.345, field_name="Naharkatiya"),
    )

    dist_meters = calculate_distance(well_a.well_id, well_b.well_id, db_session)
    assert 30000.0 <= dist_meters <= 32000.0


def test_calculate_distance_missing_well_raises_not_found(db_session):
    well_a = well_repo.create_well(
        db_session,
        WellCreate(name="WellA", latitude=27.0, longitude=95.0, field_name="Digboi"),
    )
    fake_id = uuid.uuid4()
    with pytest.raises(NotFoundError):
        calculate_distance(well_a.well_id, fake_id, db_session)


def test_find_nearby_wells_delegation(db_session):
    well_a = well_repo.create_well(
        db_session,
        WellCreate(name="CenterWell", latitude=27.4, longitude=95.5, field_name="Digboi"),
    )
    well_b = well_repo.create_well(
        db_session,
        WellCreate(name="NearWell", latitude=27.42, longitude=95.52, field_name="Digboi"),
    )
    results = find_nearby_wells(well_a.well_id, radius_km=10.0, db=db_session)
    assert len(results) == 1
    assert results[0].name == "NearWell"


def test_maintain_spatial_index_runs_without_error(db_session):
    # On non-PostgreSQL (e.g. SQLite), maintain_spatial_index safely no-ops without error
    maintain_spatial_index(db_session)
