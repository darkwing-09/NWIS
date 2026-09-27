from collections import defaultdict
from typing import List, Optional
import uuid
from sqlalchemy.orm import Session

from config.errors import WellNotFoundError
from config.settings import get_settings
from database.models.events import Event
from database.models.wells import Well, WellFormationInterval
from database.repositories import events as event_repo
from database.repositories import formations as formation_repo
from database.repositories import wells as well_repo
from domain.logging.logger import get_logger
from domain.models.correlation import CorrelationResult, EventGroup
from domain.models.enums import normalize_event_type
from domain.models.wells import WellRef

logger = get_logger("services.correlation.engine")


def find_nearby_wells(
    well_id: uuid.UUID,
    radius_km: float,
    db: Session,
) -> List[WellRef]:
    """Find active offset wells within radius_km using spatial query layer."""
    return well_repo.query_nearby(db, well_id=well_id, radius_km=radius_km, status="active")


def find_matching_formation_intervals(
    well_ids: List[uuid.UUID],
    formation: str,
    db: Session,
) -> List[WellFormationInterval]:
    """Restrict correlation to wells that actually penetrated the target formation."""
    if not well_ids or not formation:
        return []
    return formation_repo.query_by_wells_and_formation(db, well_ids=well_ids, formation=formation)


def find_events_in_depth_window(
    well_ids: List[uuid.UUID],
    depth: float,
    band_m: float,
    db: Session,
) -> List[Event]:
    """Find active events within ±band_m depth window on the matched wells."""
    if not well_ids:
        return []
    depth_min = depth - band_m
    depth_max = depth + band_m
    return event_repo.query_by_wells_and_depth_range(
        db, well_ids=well_ids, depth_min=depth_min, depth_max=depth_max
    )


def normalize_event_types(events: List[Event]) -> List[Event]:
    """
    Map event types to canonical EventType enum values.
    Defensive second gate: logs and excludes unknown/invalid types.
    """
    valid_events: List[Event] = []
    for ev in events:
        norm = normalize_event_type(ev.event_type)
        if norm is not None:
            ev.event_type = norm.value
            valid_events.append(ev)
        else:
            logger.warning(
                "Excluding event with unrecognized type from correlation",
                event_id=str(ev.event_id),
                raw_type=ev.event_type,
            )
    return valid_events


def group_events_by_pattern(events: List[Event]) -> List[EventGroup]:
    """
    Group events by canonical event_type and count distinct contributing wells.
    Pure in-memory grouping without database round-trips.
    """
    type_wells: defaultdict[str, set[uuid.UUID]] = defaultdict(set)
    type_event_ids: defaultdict[str, list[uuid.UUID]] = defaultdict(list)

    for ev in events:
        type_wells[ev.event_type].add(ev.well_id)
        type_event_ids[ev.event_type].append(ev.event_id)

    groups: List[EventGroup] = []
    for ev_type, wells in type_wells.items():
        groups.append(
            EventGroup(
                event_type=ev_type,
                distinct_well_count=len(wells),
                well_ids=wells,
                event_ids=type_event_ids[ev_type],
            )
        )
    return groups


def calculate_pattern_strength(
    groups: List[EventGroup],
    db: Optional[Session] = None,
) -> List[CorrelationResult]:
    """
    Attach distinct well count and contributing well names.
    Correlation remains a pure fact-finder; risk judgment is left to Module 13.
    """
    if not groups:
        return []

    # Map well IDs to well names if db session is provided
    all_well_ids = set()
    for g in groups:
        all_well_ids.update(g.well_ids)

    well_name_map = {}
    if db is not None and all_well_ids:
        wells = db.query(Well).filter(Well.well_id.in_(all_well_ids)).all()
        well_name_map = {w.well_id: w.name for w in wells}

    results: List[CorrelationResult] = []
    for g in groups:
        contributing_names = [well_name_map.get(wid, str(wid)) for wid in sorted(g.well_ids)]
        results.append(
            CorrelationResult(
                event_type=g.event_type,
                distinct_well_count=g.distinct_well_count,
                contributing_wells=contributing_names,
                contributing_well_ids=sorted(list(g.well_ids)),
                event_ids=g.event_ids,
            )
        )

    # Sort descending by distinct well count for clear prioritization
    results.sort(key=lambda r: r.distinct_well_count, reverse=True)
    return results


def run_correlation(
    well_id: uuid.UUID,
    depth: float,
    formation: str,
    radius_km: Optional[float] = None,
    depth_band_m: Optional[float] = None,
    db: Session = None,  # type: ignore[assignment]
) -> List[CorrelationResult]:
    """
    Deterministic cross-well correlation orchestrator.
    Chains: nearby wells -> formation match -> depth window -> normalize -> group -> calculate strength.
    Zero LLM calls anywhere in this module.
    """
    origin_well = well_repo.get_well_by_id(db, well_id)
    if not origin_well:
        raise WellNotFoundError(f"Origin well '{well_id}' not found for correlation")

    settings = get_settings()
    search_radius = radius_km if radius_km is not None else settings.correlation_default_radius_km
    band = depth_band_m if depth_band_m is not None else settings.correlation_depth_band_m

    # 1. Find nearby active wells
    nearby = find_nearby_wells(well_id, radius_km=search_radius, db=db)
    if not nearby:
        return []

    nearby_ids = [w.well_id for w in nearby]

    # 2. Restrict to wells matching target formation
    formation_intervals = find_matching_formation_intervals(nearby_ids, formation=formation, db=db)
    if not formation_intervals:
        return []

    formation_matched_well_ids = list({fi.well_id for fi in formation_intervals})

    # 3. Find events in depth window on formation-matched wells
    raw_events = find_events_in_depth_window(formation_matched_well_ids, depth=depth, band_m=band, db=db)
    if not raw_events:
        return []

    # 4. Normalize event types and filter unknown types
    normalized_events = normalize_event_types(raw_events)
    if not normalized_events:
        return []

    # 5. Group events by pattern (event_type) and count distinct wells
    groups = group_events_by_pattern(normalized_events)

    # 6. Calculate pattern strength
    return calculate_pattern_strength(groups, db=db)
