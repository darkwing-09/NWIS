import uuid
from typing import List
from sqlalchemy import text
from sqlalchemy.orm import Session

from config.errors import NotFoundError
from database.repositories import wells as well_repo
from domain.models.wells import WellRef


def calculate_distance(well_a_id: uuid.UUID, well_b_id: uuid.UUID, db: Session) -> float:
    """
    Calculate the great circle distance in meters between two wells.
    Uses PostGIS ST_Distance if on PostgreSQL, or haversine formula fallback.
    """
    well_a = well_repo.get_well_by_id(db, well_a_id)
    if not well_a:
        raise NotFoundError(f"Well '{well_a_id}' not found", detail={"well_id": str(well_a_id)})

    well_b = well_repo.get_well_by_id(db, well_b_id)
    if not well_b:
        raise NotFoundError(f"Well '{well_b_id}' not found", detail={"well_id": str(well_b_id)})

    if db.bind and db.bind.dialect.name == "postgresql":
        try:
            stmt = text(
                """
                SELECT ST_Distance(
                    (SELECT geom FROM wells WHERE well_id = :id_a),
                    (SELECT geom FROM wells WHERE well_id = :id_b)
                ) AS distance_meters
                """
            )
            result = db.execute(stmt, {"id_a": str(well_a_id), "id_b": str(well_b_id)}).scalar()
            if result is not None:
                return float(result)
        except Exception:
            db.rollback()

    dist_km = well_repo.haversine_km(
        well_a.latitude, well_a.longitude, well_b.latitude, well_b.longitude
    )
    return round(dist_km * 1000.0, 2)


def find_nearby_wells(
    well_id: uuid.UUID, radius_km: float, status: str = "active", db: Session = None  # type: ignore[assignment]
) -> List[WellRef]:
    """Retrieve nearby active wells within radius_km."""
    return well_repo.query_nearby(db, well_id, radius_km, status=status)


def maintain_spatial_index(db: Session) -> None:
    """
    Maintenance task: Analyzes or reindexes the spatial and text indexes on wells table.
    Safe to execute periodically on PostgreSQL.
    """
    if db.bind and db.bind.dialect.name == "postgresql":
        try:
            db.execute(text("ANALYZE wells"))
            db.commit()
        except Exception:
            db.rollback()
            raise
