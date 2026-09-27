from datetime import datetime
from typing import List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field


def compute_depth_band(depth: float, band_size: float = 50.0) -> str:
    """Computes depth band string, e.g. 2450.0 -> '2450-2500'."""
    lower = int(depth // band_size) * int(band_size)
    upper = lower + int(band_size)
    return f"{lower}-{upper}"


def make_dedup_key(well_id: uuid.UUID, depth_band: str, event_type: str) -> str:
    """Constructs unique dedup key for active alert suppression."""
    return f"{well_id}:{depth_band}:{event_type.lower()}"


class AlertSchema(BaseModel):
    alert_id: uuid.UUID
    well_id: uuid.UUID
    risk_level: str
    status: str
    contributing_wells: List[str] = Field(default_factory=list)
    recommended_mitigation: Optional[str] = None
    acknowledged_by: Optional[uuid.UUID] = None
    acknowledged_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    dedup_key: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class AlertAcknowledgeRequest(BaseModel):
    note: Optional[str] = None


class AlertResolveRequest(BaseModel):
    resolution_notes: Optional[str] = None
