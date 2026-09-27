from typing import List, Set
import uuid
from pydantic import BaseModel, ConfigDict, Field


class EventGroup(BaseModel):
    """Pure grouping of events by event_type with distinct contributing wells."""

    event_type: str
    distinct_well_count: int
    well_ids: Set[uuid.UUID]
    event_ids: List[uuid.UUID]

    model_config = ConfigDict(arbitrary_types_allowed=True)


class CorrelationResult(BaseModel):
    """Deterministic cross-well pattern discovery output."""

    event_type: str
    distinct_well_count: int
    contributing_wells: List[str] = Field(default_factory=list, description="Well names of contributing offset wells")
    contributing_well_ids: List[uuid.UUID] = Field(default_factory=list)
    event_ids: List[uuid.UUID] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)
