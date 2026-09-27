import uuid
from typing import List
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.wells import WellFormationInterval
from domain.models.wells import FormationIntervalCreate


def create_interval(db: Session, well_id: uuid.UUID, data: FormationIntervalCreate) -> WellFormationInterval:
    """Create a new well formation interval."""
    interval = WellFormationInterval(
        interval_id=uuid.uuid4(),
        well_id=well_id,
        formation_name=data.formation_name,
        top_depth=data.top_depth,
        bottom_depth=data.bottom_depth,
    )
    db.add(interval)
    db.commit()
    db.refresh(interval)
    return interval


def list_intervals_for_well(db: Session, well_id: uuid.UUID) -> List[WellFormationInterval]:
    """List all formation intervals for a well, ordered by top_depth."""
    stmt = (
        select(WellFormationInterval)
        .where(WellFormationInterval.well_id == well_id)
        .order_by(WellFormationInterval.top_depth.asc())
    )
    return list(db.execute(stmt).scalars().all())


def query_by_wells_and_formation(
    db: Session, well_ids: List[uuid.UUID], formation: str
) -> List[WellFormationInterval]:
    """Find matching formation intervals across multiple wells."""
    if not well_ids:
        return []
    stmt = (
        select(WellFormationInterval)
        .where(
            WellFormationInterval.well_id.in_(well_ids),
            WellFormationInterval.formation_name.ilike(formation.strip()),
        )
        .order_by(WellFormationInterval.top_depth.asc())
    )
    return list(db.execute(stmt).scalars().all())
