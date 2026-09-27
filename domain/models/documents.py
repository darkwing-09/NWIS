import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class DocumentUploadResponse(BaseModel):
    doc_id: uuid.UUID
    status: str = "pending"


class DocumentDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    doc_id: uuid.UUID
    well_id: Optional[uuid.UUID] = None
    doc_type: str
    file_path: str
    ocr_status: str
    extraction_status: str
    confidence: Optional[float] = None
    amends_document_id: Optional[uuid.UUID] = None
    data_classification: str = "internal"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DocumentStatus(BaseModel):
    doc_id: uuid.UUID
    ocr_status: str
    extraction_status: str
    needs_review_count: int = 0
