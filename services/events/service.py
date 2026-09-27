from typing import Optional
import uuid
from sqlalchemy.orm import Session

from config.errors import AuthorizationError, WellNotFoundError
from database.models.wells import Well
from database.repositories.events import list_events
from domain.models.auth import AuthenticatedUser
from domain.models.events import EventResponse, PaginatedEvents


def list_events_for_well(
    db: Session,
    well_id: uuid.UUID,
    user: AuthenticatedUser,
    event_type: Optional[str] = None,
    depth_min: Optional[float] = None,
    depth_max: Optional[float] = None,
    page: int = 1,
    page_size: int = 50,
) -> PaginatedEvents:
    """Retrieve paginated events for a specific well, scoped by user's field authorization."""
    well = db.get(Well, well_id)
    if not well:
        raise WellNotFoundError(f"Well {well_id} not found")

    if not user.can_access_field(well.field_name):
        raise AuthorizationError(f"User not authorized to access well in field '{well.field_name}'")

    events, total = list_events(
        db,
        well_id=well_id,
        event_type=event_type,
        depth_min=depth_min,
        depth_max=depth_max,
        page=page,
        page_size=page_size,
    )

    items = []
    for ev in events:
        resp = EventResponse(
            event_id=ev.event_id,
            well_id=ev.well_id,
            depth=ev.depth,
            event_type=ev.event_type,
            severity=ev.severity,
            description_raw=ev.description_raw,
            mitigation_taken=ev.mitigation_taken,
            source_document_id=ev.source_document_id,
            confidence_score=ev.confidence_score,
            extractor_version=ev.extractor_version,
            model_version=ev.model_version,
            superseded_by_event_id=ev.superseded_by_event_id,
            duplicate_group_id=ev.duplicate_group_id,
            valid_from=ev.valid_from,
            valid_to=ev.valid_to,
            created_at=ev.created_at,
            cause=ev.incident.cause if ev.incident else None,
            outcome=ev.incident.outcome if ev.incident else None,
            auto_title=ev.incident.auto_title if ev.incident else None,
        )
        items.append(resp)

    return PaginatedEvents(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )
