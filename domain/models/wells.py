import uuid
from datetime import date, datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


from domain.models.auth import AuthenticatedUser


class WellCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    spud_date: Optional[date] = None
    operator_name: Optional[str] = None
    field_name: Optional[str] = None
    basin_name: Optional[str] = None


class WellUpdate(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None
    operator_name: Optional[str] = None
    field_name: Optional[str] = None
    basin_name: Optional[str] = None


class WellRef(BaseModel):
    well_id: uuid.UUID
    name: str
    status: str
    latitude: float
    longitude: float
    field_name: Optional[str] = None
    basin_name: Optional[str] = None
    distance_km: Optional[float] = None

    model_config = ConfigDict(from_attributes=True)


class FormationIntervalCreate(BaseModel):
    formation_name: str = Field(..., min_length=1)
    top_depth: float = Field(..., ge=0)
    bottom_depth: float = Field(..., ge=0)


class FormationIntervalRef(BaseModel):
    interval_id: uuid.UUID
    well_id: uuid.UUID
    formation_name: str
    top_depth: float
    bottom_depth: float

    model_config = ConfigDict(from_attributes=True)
