from datetime import date
from typing import Dict, Optional, Type
from pydantic import BaseModel, Field, ValidationInfo, field_validator

from domain.models.enums import EventType


class WellMetadataSchema(BaseModel):
    well_name: Optional[str] = None
    spud_date: Optional[date] = None
    operator: Optional[str] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class DepthEventSchema(BaseModel):
    event_type: EventType
    depth: float = Field(gt=0, lt=15000, description="Sanity bound: depth in meters must be >0 and <15000")
    description: str = Field(min_length=1)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class FormationSchema(BaseModel):
    formation_name: str = Field(min_length=1)
    top_depth: float = Field(ge=0, lt=15000)
    bottom_depth: float = Field(gt=0, lt=15000)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("bottom_depth")
    @classmethod
    def bottom_after_top(cls, v: float, info: ValidationInfo) -> float:
        top = info.data.get("top_depth")
        if top is not None and v <= top:
            raise ValueError(f"bottom_depth ({v}m) must be strictly greater than top_depth ({top}m)")
        return v


class IncidentSchema(BaseModel):
    cause: Optional[str] = None
    depth: Optional[float] = Field(default=None, gt=0, lt=15000)
    mitigation: Optional[str] = None
    outcome: Optional[str] = None
    description: str = Field(min_length=1)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class MudCasingSchema(BaseModel):
    depth: float = Field(gt=0, lt=15000)
    mud_weight: Optional[float] = Field(default=None, gt=0)
    casing_size: Optional[str] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


SCHEMA_REGISTRY: Dict[str, Type[BaseModel]] = {
    "well_metadata": WellMetadataSchema,
    "depth_events": DepthEventSchema,
    "formations": FormationSchema,
    "incidents": IncidentSchema,
    "mud_casing": MudCasingSchema,
}
