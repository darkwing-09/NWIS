from typing import List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from config.settings import get_settings
from services.ertmac.context_service import ContextService, get_context_service
from services.search.filters import apply_nearby_filter


class ParsedQuery(BaseModel):
    text: str
    well_id: Optional[uuid.UUID] = None
    formation: Optional[str] = None
    nearby_well_ids: Optional[List[uuid.UUID]] = Field(default=None)

    model_config = ConfigDict(from_attributes=True)


def parse_query(
    raw_query: str,
    well_id: Optional[uuid.UUID] = None,
    formation: Optional[str] = None,
    db: Optional[Session] = None,
    context_service: Optional[ContextService] = None,
    radius_km: Optional[float] = None,
) -> ParsedQuery:
    """
    Parses and normalizes search query text and resolves active-well context:
    1. Normalizes raw query (strip).
    2. Resolves formation from active context if well_id given but formation is omitted.
    3. Scopes candidate set by nearby wells if well_id and db are provided.
    """
    clean_text = raw_query.strip()
    resolved_formation = formation
    nearby_ids: Optional[List[uuid.UUID]] = None

    if well_id:
        if not resolved_formation:
            svc = context_service or get_context_service()
            current = svc.get_current(well_id)
            if current and current.formation:
                resolved_formation = current.formation

        if db is not None:
            settings = get_settings()
            r_km = radius_km if radius_km is not None else settings.correlation_default_radius_km
            nearby_ids = apply_nearby_filter(well_id=well_id, radius_km=r_km, db=db)

    return ParsedQuery(
        text=clean_text,
        well_id=well_id,
        formation=resolved_formation,
        nearby_well_ids=nearby_ids,
    )
