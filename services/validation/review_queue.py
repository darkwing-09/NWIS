from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import uuid
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config.errors import NotFoundError, ValidationError
from database.models.documents import Document, Extraction, ExtractionError
from database.models.events import Event, IncidentEvent
from domain.models.auth import AuthenticatedUser
from services.validation.gate import validate


def enqueue(
    extraction_id: uuid.UUID,
    raw_output: Dict[str, Any],
    error: str,
    db: Session,
) -> ExtractionError:
    """Enqueue an invalid extraction into the review queue by creating an ExtractionError row."""
    # Ensure Extraction status is updated
    extraction = db.get(Extraction, extraction_id)
    if extraction:
        extraction.status = "failed"

    error_row = ExtractionError(
        error_id=uuid.uuid4(),
        extraction_id=extraction_id,
        error_detail=error,
        created_at=datetime.now(timezone.utc),
    )
    db.add(error_row)
    db.commit()
    db.refresh(error_row)
    return error_row


def list_review_queue(
    db: Session,
    page: int = 1,
    page_size: int = 20,
    user: Optional[AuthenticatedUser] = None,
) -> Tuple[List[ExtractionError], int]:
    """List extraction errors requiring human review, paginated."""
    count_stmt = select(func.count(ExtractionError.error_id))
    total = db.scalar(count_stmt) or 0

    offset = max(0, (page - 1) * page_size)
    stmt = (
        select(ExtractionError)
        .order_by(ExtractionError.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    items = list(db.execute(stmt).scalars().all())
    return items, total


def approve(
    extraction_error_id: uuid.UUID,
    corrected_fields: Dict[str, Any],
    db: Session,
    user: Optional[AuthenticatedUser] = None,
) -> Event:
    """Approve/edit reviewed extraction. Human corrections MUST pass the same schema gate."""
    err_row = db.get(ExtractionError, extraction_error_id)
    if not err_row:
        raise NotFoundError("Review queue item not found")

    extraction = db.get(Extraction, err_row.extraction_id)
    if not extraction:
        raise NotFoundError("Extraction record not found")

    extractor_name = extraction.extractor_name
    # Pass corrected fields through the exact same schema gate
    val_result = validate(extraction.extraction_id, extractor_name, corrected_fields, db=None)
    if not val_result.passed:
        raise ValidationError(f"Corrected fields failed schema gate: {val_result.error}")

    data = val_result.data or {}

    # Get parent document
    doc = db.get(Document, extraction.doc_id)
    well_id = doc.well_id if doc else None
    if not well_id:
        raise ValidationError("Cannot persist event without associated well_id on document")

    # Persist the approved event
    event = Event(
        event_id=uuid.uuid4(),
        well_id=well_id,
        depth=float(data.get("depth", 1000.0)),
        event_type=str(data.get("event_type", "other")),
        severity=str(data.get("severity", "medium")),
        description_raw=str(data.get("description", "Approved from review queue")),
        source_document_id=extraction.doc_id,
        confidence_score=1.0,  # Human approved
        extractor_version=f"human_review_{user.user_id if user else 'admin'}",
        model_version=None,
    )
    db.add(event)

    # Mark extraction as approved and remove error from queue
    extraction.status = "approved"
    db.delete(err_row)
    db.commit()
    db.refresh(event)
    return event
