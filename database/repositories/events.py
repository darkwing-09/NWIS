from datetime import datetime, timezone
from typing import List, Optional, Tuple
import uuid
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config.errors import NotFoundError
from database.models.events import Event, IncidentEvent
from domain.models.enums import EventType, normalize_event_type
from domain.models.events import EventCreate, IncidentCreate, Provenance


def find_duplicate_candidates(
    db: Session,
    well_id: uuid.UUID,
    depth: float,
    event_type: EventType | str,
    band_m: float = 5.0,
) -> List[Event]:
    """Find potential duplicate events on the same well within depth ± band_m having matching event_type."""
    ev_type_str = event_type.value if isinstance(event_type, EventType) else str(event_type)
    norm = normalize_event_type(ev_type_str)
    canonical_type = norm.value if norm else ev_type_str.lower()

    stmt = (
        select(Event)
        .where(
            Event.well_id == well_id,
            func.lower(Event.event_type) == canonical_type,
            Event.depth >= (depth - band_m),
            Event.depth <= (depth + band_m),
            Event.superseded_by_event_id.is_(None),
        )
        .order_by(Event.depth.asc())
    )
    return list(db.execute(stmt).scalars().all())


def link_duplicate_group(db: Session, event_ids: List[uuid.UUID]) -> uuid.UUID:
    """Link multiple events into a shared duplicate group."""
    if not event_ids:
        return uuid.uuid4()

    # Find existing group id among the events, if any
    existing_group_stmt = (
        select(Event.duplicate_group_id)
        .where(Event.event_id.in_(event_ids), Event.duplicate_group_id.is_not(None))
        .limit(1)
    )
    existing_group = db.scalar(existing_group_stmt)
    group_id = existing_group or uuid.uuid4()

    events_stmt = select(Event).where(Event.event_id.in_(event_ids))
    events = db.execute(events_stmt).scalars().all()
    for ev in events:
        ev.duplicate_group_id = group_id

    db.commit()
    return group_id


def create_event(
    db: Session,
    data: EventCreate,
    provenance: Provenance,
    incident_data: Optional[IncidentCreate] = None,
) -> Event:
    """Create and persist an event with full provenance and write-time duplicate detection."""
    ev_type_str = data.event_type.value if isinstance(data.event_type, EventType) else str(data.event_type)
    norm = normalize_event_type(ev_type_str)
    canonical_type = norm.value if norm else ev_type_str

    event_id = uuid.uuid4()
    now_utc = datetime.now(timezone.utc)

    # 1. Check for duplicate candidates within ±5m
    candidates = find_duplicate_candidates(
        db,
        well_id=data.well_id,
        depth=data.depth,
        event_type=canonical_type,
        band_m=5.0,
    )

    duplicate_group_id: Optional[uuid.UUID] = None
    if candidates:
        existing_group = next((c.duplicate_group_id for c in candidates if c.duplicate_group_id), None)
        duplicate_group_id = existing_group or uuid.uuid4()
        for cand in candidates:
            cand.duplicate_group_id = duplicate_group_id

    # 2. Create Event row
    event = Event(
        event_id=event_id,
        well_id=data.well_id,
        depth=data.depth,
        event_type=canonical_type,
        severity=data.severity,
        description_raw=data.description_raw,
        mitigation_taken=data.mitigation_taken,
        source_document_id=provenance.source_document_id,
        confidence_score=data.confidence_score,
        extractor_version=provenance.extractor_version,
        model_version=provenance.model_version,
        valid_from=data.valid_from or now_utc,
        valid_to=None,
        duplicate_group_id=duplicate_group_id,
    )
    db.add(event)

    # 3. Optional Incident row
    if incident_data:
        inc = IncidentEvent(
            event_id=event_id,
            cause=incident_data.cause,
            outcome=incident_data.outcome,
            auto_title=incident_data.auto_title,
        )
        db.add(inc)

    db.commit()
    db.refresh(event)
    return event


def supersede_event(
    db: Session,
    old_event_id: uuid.UUID,
    new_event_data: EventCreate,
    provenance: Provenance,
    incident_data: Optional[IncidentCreate] = None,
) -> Event:
    """
    Amendment path: creates new row and marks old event as superseded.
    NEVER deletes or overwrites old_event_id's row (Part 8 rule).
    """
    old_event = db.get(Event, old_event_id)
    if not old_event:
        raise NotFoundError(f"Event {old_event_id} not found to supersede")

    now_utc = datetime.now(timezone.utc)
    new_event_id = uuid.uuid4()

    ev_type_str = new_event_data.event_type.value if isinstance(new_event_data.event_type, EventType) else str(new_event_data.event_type)
    norm = normalize_event_type(ev_type_str)
    canonical_type = norm.value if norm else ev_type_str

    new_event = Event(
        event_id=new_event_id,
        well_id=new_event_data.well_id,
        depth=new_event_data.depth,
        event_type=canonical_type,
        severity=new_event_data.severity,
        description_raw=new_event_data.description_raw,
        mitigation_taken=new_event_data.mitigation_taken,
        source_document_id=provenance.source_document_id,
        confidence_score=new_event_data.confidence_score,
        extractor_version=provenance.extractor_version,
        model_version=provenance.model_version,
        valid_from=new_event_data.valid_from or now_utc,
        valid_to=None,
        duplicate_group_id=old_event.duplicate_group_id,
    )
    db.add(new_event)

    if incident_data:
        inc = IncidentEvent(
            event_id=new_event_id,
            cause=incident_data.cause,
            outcome=incident_data.outcome,
            auto_title=incident_data.auto_title,
        )
        db.add(inc)

    # Link old event to new event
    old_event.superseded_by_event_id = new_event_id
    old_event.valid_to = new_event.valid_from

    db.commit()
    db.refresh(new_event)
    return new_event


def query_by_wells_and_depth_range(
    db: Session,
    well_ids: List[uuid.UUID],
    depth_min: float,
    depth_max: float,
) -> List[Event]:
    """
    Query active (non-superseded) events on given wells within depth range.
    Backs Module 12 find_events_in_depth_window.
    """
    if not well_ids:
        return []

    stmt = (
        select(Event)
        .where(
            Event.well_id.in_(well_ids),
            Event.depth >= depth_min,
            Event.depth <= depth_max,
            Event.superseded_by_event_id.is_(None),
        )
        .order_by(Event.well_id.asc(), Event.depth.asc())
    )
    return list(db.execute(stmt).scalars().all())


def list_events(
    db: Session,
    well_id: uuid.UUID,
    event_type: Optional[str] = None,
    depth_min: Optional[float] = None,
    depth_max: Optional[float] = None,
    page: int = 1,
    page_size: int = 50,
) -> Tuple[List[Event], int]:
    """Query paginated events for a well, with optional type and depth filtering."""
    filters = [
        Event.well_id == well_id,
        Event.superseded_by_event_id.is_(None),
    ]

    if event_type:
        norm = normalize_event_type(event_type)
        val = norm.value if norm else event_type.lower()
        filters.append(func.lower(Event.event_type) == val)

    if depth_min is not None:
        filters.append(Event.depth >= depth_min)
    if depth_max is not None:
        filters.append(Event.depth <= depth_max)

    count_stmt = select(func.count(Event.event_id)).where(*filters)
    total = db.scalar(count_stmt) or 0

    offset = max(0, (page - 1) * page_size)
    stmt = (
        select(Event)
        .where(*filters)
        .order_by(Event.depth.asc(), Event.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    items = list(db.execute(stmt).scalars().all())
    return items, total
