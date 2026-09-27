from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field

from domain.models.correlation import CorrelationResult


class RiskAssessmentSchema(BaseModel):
    assessment_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    well_id: uuid.UUID
    risk_level: str  # low, medium, high
    confidence: Optional[float] = None
    method: str = "rule_based_v1"
    contributing_evidence: CorrelationResult
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(from_attributes=True)


class RiskFeatures(BaseModel):
    well_id: uuid.UUID
    depth: float
    formation: str
    event_type: str
    distinct_well_count: int
    offset_distances_km: List[float] = Field(default_factory=list)


class RiskPrediction(BaseModel):
    event_type: str
    predicted_risk_level: str  # low, medium, high
    calibrated_probability: float = Field(ge=0.0, le=1.0)
    model_version: str


class EvaluationReport(BaseModel):
    report_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    model_name: str
    model_version: str
    approved: bool = False
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    metrics: Dict[str, Any] = Field(default_factory=dict)
