import uuid
from typing import List
from pydantic import BaseModel, ConfigDict


class DocumentClass(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    doc_type: str  # WCR or DDR
    page_count: int


class TextLayerResult(BaseModel):
    page_flags: List[bool]  # True = has native text layer, False = scanned


class OCRPageResult(BaseModel):
    page_index: int
    text: str
    confidence: float


class OCRResult(BaseModel):
    document_id: uuid.UUID
    confidence: float
    text: str
    low_confidence_pages: List[int]
