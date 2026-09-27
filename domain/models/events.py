from datetime import datetime
from typing import List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field

from domain.models.enums import EventType


class Provenance(BaseModel):
    extractor_version: str = "1.0.0"
    model_version: Optional[str] = "nwis-extractor-v1"
    source_document_id: Optional[uuid.UUID] = None


class EventCreate(BaseModel):
    well_id: uuid.UUID
    depth: float = Field(gt=0, lt=15000)
    event_type: str
    severity: str = "medium"
    description_raw: str
    mitigation_taken: Optional[str] = None
    confidence_score: float = Field(default=1.0, ge=0.0, le=1.0)
    valid_from: Optional[datetime] = None


class IncidentCreate(BaseModel):
    cause: Optional[str] = None
    outcome: Optional[str] = None
    auto_title: Optional[str] = None


class EventResponse(BaseModel):
    event_id: uuid.UUID
    well_id: uuid.UUID
    depth: float
    event_type: str
    severity: str
    description_raw: str
    mitigation_taken: Optional[str] = None
    source_document_id: Optional[uuid.UUID] = None
    confidence_score: float
    extractor_version: str
    model_version: Optional[str] = None
    superseded_by_event_id: Optional[uuid.UUID] = None
    duplicate_group_id: Optional[uuid.UUID] = None
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    created_at: Optional[datetime] = None
    cause: Optional[str] = None
    outcome: Optional[str] = None
    auto_title: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)



class PaginatedEvents(BaseModel):
    items: List[EventResponse]
    total: int
    page: int
    page_size: int
