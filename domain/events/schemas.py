import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class BaseEvent(BaseModel):
    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    event_type: str
    version: str = "v1"
    emitted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trace_id: Optional[str] = None


class DocumentUploadedPayload(BaseModel):
    document_id: uuid.UUID
    well_id: uuid.UUID
    file_path: str
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DocumentUploaded(BaseEvent):
    event_type: str = "DocumentUploaded"
    payload: DocumentUploadedPayload


class ExtractionCompletedPayload(BaseModel):
    document_id: uuid.UUID
    extraction_id: uuid.UUID
    raw_output: Dict[str, Any]


class ExtractionCompleted(BaseEvent):
    event_type: str = "ExtractionCompleted"
    payload: ExtractionCompletedPayload


class ExtractionNeedsReviewPayload(BaseModel):
    document_id: uuid.UUID
    extraction_id: uuid.UUID
    error_detail: str


class ExtractionNeedsReview(BaseEvent):
    event_type: str = "ExtractionNeedsReview"
    payload: ExtractionNeedsReviewPayload


class WellContextUpdatedPayload(BaseModel):
    well_id: uuid.UUID
    depth: float
    formation: Optional[str] = None
    timestamp: datetime


class WellContextUpdated(BaseEvent):
    event_type: str = "WellContextUpdated"
    payload: WellContextUpdatedPayload


class CorrelationCompletedPayload(BaseModel):
    well_id: uuid.UUID
    correlation_results: List[Dict[str, Any]]


class CorrelationCompleted(BaseEvent):
    event_type: str = "CorrelationCompleted"
    payload: CorrelationCompletedPayload


class RiskAssessmentCreatedPayload(BaseModel):
    well_id: uuid.UUID
    risk_level: str
    confidence: float
    method: str
    correlation_result: Dict[str, Any]


class RiskAssessmentCreated(BaseEvent):
    event_type: str = "RiskAssessmentCreated"
    payload: RiskAssessmentCreatedPayload


class AlertCreatedPayload(BaseModel):
    alert_id: uuid.UUID
    well_id: uuid.UUID
    risk_level: str
    contributing_wells: List[str]


class AlertCreated(BaseEvent):
    event_type: str = "AlertCreated"
    payload: AlertCreatedPayload


class AlertAcknowledgedPayload(BaseModel):
    alert_id: uuid.UUID
    user_id: uuid.UUID
    acknowledged_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AlertAcknowledged(BaseEvent):
    event_type: str = "AlertAcknowledged"
    payload: AlertAcknowledgedPayload
