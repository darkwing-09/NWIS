from typing import List
import uuid
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models.documents import Document
from services.wells.geospatial import find_nearby_wells


def apply_nearby_filter(
    well_id: uuid.UUID,
    radius_km: float,
    db: Session,
) -> List[uuid.UUID]:
    """
    Returns list of well IDs within radius_km of the active well.
    Prevents irrelevant, distant wells from polluting RAG candidate sets.
    """
    nearby_wells = find_nearby_wells(well_id=well_id, radius_km=radius_km, db=db)
    return [w.well_id for w in nearby_wells]


def exclude_superseded(
    document_ids: List[uuid.UUID],
    db: Session,
) -> List[uuid.UUID]:
    """
    Filters out documents that are the OLD side of an amends_document_id relationship.
    Prevents stale, amended WCR/DDR information from surfacing in search results.
    """
    if not document_ids:
        return []

    # Subquery: any doc_id that is amended by another document
    stmt = (
        select(Document.amends_document_id)
        .where(Document.amends_document_id.isnot(None))
        .where(Document.amends_document_id.in_(document_ids))
    )
    superseded_ids = set(db.scalars(stmt).all())

    return [did for did in document_ids if did not in superseded_ids]
