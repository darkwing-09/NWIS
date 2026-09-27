from datetime import date
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, Field

from domain.models.enums import EventType


class WellMetadataExtraction(BaseModel):
    well_name: Optional[str] = None
    spud_date: Optional[date] = None
    operator: Optional[str] = None
    matched_well_id: Optional[uuid.UUID] = None
    match_confidence: float = 0.0


class DepthEventExtraction(BaseModel):
    event_type: str
    depth: float
    description: str
    confidence: float = 1.0
    needs_review: bool = False
    error_reason: Optional[str] = None


class FormationExtraction(BaseModel):
    formation_name: str
    top_depth: float
    bottom_depth: float
    confidence: float = 1.0
    needs_review: bool = False
    error_reason: Optional[str] = None


class IncidentExtraction(BaseModel):
    cause: Optional[str] = None
    depth: Optional[float] = None
    mitigation: Optional[str] = None
    outcome: Optional[str] = None
    description: str
    auto_title: Optional[str] = None
    confidence: float = 1.0


class ExtractionResult(BaseModel):
    document_id: uuid.UUID
    well_metadata: Optional[WellMetadataExtraction] = None
    depth_events: List[DepthEventExtraction] = Field(default_factory=list)
    formations: List[FormationExtraction] = Field(default_factory=list)
    incidents: List[IncidentExtraction] = Field(default_factory=list)
    events_created: int = 0
    needs_review_count: int = 0
    status: str = "done"  # done, needs_review, failed
