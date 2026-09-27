import math
import uuid
from typing import List, Optional
from difflib import SequenceMatcher
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from database.models.wells import Well
from domain.models.wells import WellCreate, WellRef


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great circle distance in kilometers between two points."""
    r = 6371.0  # Earth radius in kilometers
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r * c


def create_well(db: Session, data: WellCreate) -> Well:
    """Create and persist a new Well."""
    well = Well(
        well_id=uuid.uuid4(),
        name=data.name,
        latitude=data.latitude,
        longitude=data.longitude,
        status="active",
        spud_date=data.spud_date,
        operator_name=data.operator_name,
        field_name=data.field_name,
        basin_name=data.basin_name,
    )
    # Set geom if dialect is PostgreSQL with PostGIS
    if db.bind and db.bind.dialect.name == "postgresql":
        well.geom = f"SRID=4326;POINT({data.longitude} {data.latitude})"

    db.add(well)
    db.commit()
    db.refresh(well)
    return well


def get_well_by_id(db: Session, well_id: uuid.UUID) -> Optional[Well]:
    """Retrieve well by ID."""
    return db.execute(select(Well).where(Well.well_id == well_id)).scalar_one_or_none()


def list_wells(
    db: Session,
    field: Optional[str] = None,
    status: Optional[str] = None,
    allowed_fields: Optional[List[str]] = None,
) -> List[Well]:
    """Query wells with optional filtering and field-level RBAC filtering."""
    stmt = select(Well)
    if status is not None:
        stmt = stmt.where(Well.status == status)
    if field is not None:
        stmt = stmt.where(Well.field_name == field)
    if allowed_fields is not None:
        stmt = stmt.where(Well.field_name.in_(allowed_fields))

    return list(db.execute(stmt).scalars().all())


def fuzzy_match_well_name(db: Session, name: str, threshold: float = 0.8) -> Optional[Well]:
    """Match a well name against existing wells using pg_trgm similarity on PostgreSQL or SequenceMatcher."""
    if not name or not name.strip():
        return None

    clean_name = name.strip()

    if db.bind and db.bind.dialect.name == "postgresql":
        try:
            # Uses GIN/GiST pg_trgm index
            stmt = text(
                """
                SELECT well_id, similarity(name, :query_name) AS sml
                FROM wells
                WHERE similarity(name, :query_name) >= :threshold
                ORDER BY sml DESC
                LIMIT 1
                """
            )
            result = db.execute(stmt, {"query_name": clean_name, "threshold": threshold}).first()
            if result:
                return get_well_by_id(db, result.well_id)
        except Exception:
            db.rollback()

    # Portable in-memory fallback for unit testing / other dialects
    wells = db.execute(select(Well)).scalars().all()
    best_match: Optional[Well] = None
    best_ratio = 0.0

    target = clean_name.lower()
    for w in wells:
        ratio = SequenceMatcher(None, target, w.name.lower()).ratio()
        if ratio >= threshold and ratio > best_ratio:
            best_ratio = ratio
            best_match = w

    return best_match


def query_nearby(
    db: Session, well_id: uuid.UUID, radius_km: float, status: str = "active"
) -> List[WellRef]:
    """Query nearby wells within radius_km (using PostGIS ST_DWithin if on PG, or haversine)."""
    origin = get_well_by_id(db, well_id)
    if not origin:
        return []

    if db.bind and db.bind.dialect.name == "postgresql":
        try:
            radius_meters = radius_km * 1000.0
            stmt = text(
                """
                SELECT well_id, name, status, latitude, longitude, field_name, basin_name,
                       ST_Distance(geom, (SELECT geom FROM wells WHERE well_id = :origin_id)) / 1000.0 AS distance_km
                FROM wells
                WHERE well_id != :origin_id
                  AND status = :status
                  AND ST_DWithin(geom, (SELECT geom FROM wells WHERE well_id = :origin_id), :radius_m)
                ORDER BY distance_km ASC
                """
            )
            rows = db.execute(
                stmt,
                {"origin_id": str(well_id), "status": status, "radius_m": radius_meters},
            ).fetchall()
            return [
                WellRef(
                    well_id=row.well_id,
                    name=row.name,
                    status=row.status,
                    latitude=row.latitude,
                    longitude=row.longitude,
                    field_name=row.field_name,
                    basin_name=row.basin_name,
                    distance_km=round(row.distance_km, 3),
                )
                for row in rows
            ]
        except Exception:
            db.rollback()

    # Portable calculation fallback
    query = select(Well).where(Well.well_id != well_id)
    if status:
        query = query.where(Well.status == status)

    candidate_wells = db.execute(query).scalars().all()
    results: List[WellRef] = []

    for w in candidate_wells:
        dist = haversine_km(origin.latitude, origin.longitude, w.latitude, w.longitude)
        if dist <= radius_km:
            results.append(
                WellRef(
                    well_id=w.well_id,
                    name=w.name,
                    status=w.status,
                    latitude=w.latitude,
                    longitude=w.longitude,
                    field_name=w.field_name,
                    basin_name=w.basin_name,
                    distance_km=round(dist, 3),
                )
            )

    results.sort(key=lambda x: x.distance_km if x.distance_km is not None else 999999.0)
    return results
