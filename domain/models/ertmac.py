from datetime import datetime
from typing import Optional
import uuid
from pydantic import BaseModel, Field


class WellContext(BaseModel):
    """Real-time drilling telemetry and well context snapshot."""

    well_id: uuid.UUID
    depth: float = Field(gt=0, lt=15000)
    formation: Optional[str] = None
    timestamp: datetime
    bit_depth: Optional[float] = None
    hole_depth: Optional[float] = None
    rop: Optional[float] = None  # Rate of penetration (m/hr)
    wob: Optional[float] = None  # Weight on bit (klbs / tonnes)
    rpm: Optional[float] = None  # Rotary RPM
    spp: Optional[float] = None  # Standpipe pressure (psi)
    flow_in: Optional[float] = None
    flow_out: Optional[float] = None
    mud_weight: Optional[float] = None
