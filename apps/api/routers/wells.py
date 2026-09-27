from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database.session import get_db
from domain.models.auth import AuthenticatedUser
from domain.models.events import PaginatedEvents
from domain.models.wells import FormationIntervalRef, WellRef
from services.auth.rbac import require_permission
from services.events.service import list_events_for_well
from services.wells.geospatial import find_nearby_wells
from services.wells.service import get_well, list_formations, list_wells

router = APIRouter(prefix="/wells", tags=["wells"])


@router.get("", response_model=List[WellRef])
def get_wells(
    field: Optional[str] = Query(None, description="Filter by oilfield name"),
    status: Optional[str] = Query(None, description="Filter by well status"),
    user: AuthenticatedUser = Depends(require_permission("wells:read")),
    db: Session = Depends(get_db),
) -> List[WellRef]:
    """List wells accessible to the caller within their authorized fields."""
    wells = list_wells(user=user, field=field, status=status, db=db)
    return [
        WellRef(
            well_id=w.well_id,
            name=w.name,
            status=w.status,
            latitude=w.latitude,
            longitude=w.longitude,
            field_name=w.field_name,
            basin_name=w.basin_name,
        )
        for w in wells
    ]


@router.get("/{well_id}", response_model=WellRef)
def get_well_detail(
    well_id: uuid.UUID,
    user: AuthenticatedUser = Depends(require_permission("wells:read")),
    db: Session = Depends(get_db),
) -> WellRef:
    """Retrieve details for a specific well."""
    w = get_well(well_id, user, db)
    return WellRef(
        well_id=w.well_id,
        name=w.name,
        status=w.status,
        latitude=w.latitude,
        longitude=w.longitude,
        field_name=w.field_name,
        basin_name=w.basin_name,
    )


@router.get("/{well_id}/nearby", response_model=List[WellRef])
def get_nearby_wells(
    well_id: uuid.UUID,
    radius_km: float = Query(25.0, gt=0, le=500.0, description="Search radius in kilometers"),
    user: AuthenticatedUser = Depends(require_permission("wells:read")),
    db: Session = Depends(get_db),
) -> List[WellRef]:
    """Find nearby active offset wells within the specified radius."""
    # Ensure caller has access to the origin well
    get_well(well_id, user, db)
    return find_nearby_wells(well_id=well_id, radius_km=radius_km, db=db)


@router.get("/{well_id}/formations", response_model=List[FormationIntervalRef])
def get_well_formations(
    well_id: uuid.UUID,
    user: AuthenticatedUser = Depends(require_permission("wells:read")),
    db: Session = Depends(get_db),
) -> List[FormationIntervalRef]:
    """Retrieve geological formation intervals for a well."""
    intervals = list_formations(well_id, user, db)
    return [
        FormationIntervalRef(
            interval_id=fi.interval_id,
            well_id=fi.well_id,
            formation_name=fi.formation_name,
            top_depth=fi.top_depth,
            bottom_depth=fi.bottom_depth,
        )
        for fi in intervals
    ]


@router.get("/{well_id}/events", response_model=PaginatedEvents)
def get_well_events(
    well_id: uuid.UUID,
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    depth_min: Optional[float] = Query(None, description="Minimum depth in meters"),
    depth_max: Optional[float] = Query(None, description="Maximum depth in meters"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page"),
    user: AuthenticatedUser = Depends(require_permission("wells:read")),
    db: Session = Depends(get_db),
) -> PaginatedEvents:
    """Retrieve paginated drilling events for a well, with optional type and depth filtering."""
    return list_events_for_well(
        db=db,
        well_id=well_id,
        user=user,
        event_type=event_type,
        depth_min=depth_min,
        depth_max=depth_max,
        page=page,
        page_size=page_size,
    )
