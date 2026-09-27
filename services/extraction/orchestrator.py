from datetime import datetime, timezone
import logging
from typing import Optional
import uuid
from sqlalchemy.orm import Session

from config.errors import DocumentNotFoundError, DocumentNotOCRedError
from database.models.documents import Document, Extraction
from database.models.events import Event, IncidentEvent
from database.models.wells import WellFormationInterval
from domain.ai.llm_provider import LLMProvider
from domain.events.schemas import (
    DocumentExtractionCompleted,
    DocumentExtractionCompletedPayload,
    ExtractionNeedsReview,
    ExtractionNeedsReviewPayload,
)
from domain.models.extraction import (
    DepthEventExtraction,
    ExtractionResult,
    FormationExtraction,
    IncidentExtraction,
    WellMetadataExtraction,
)
from services.extraction.depth_events import extract_depth_events
from services.extraction.formations import extract_formations
from services.extraction.incidents import extract_incidents
from services.extraction.well_metadata import extract_well_metadata
from services.validation.gate import validate
from services.validation.review_queue import enqueue

logger = logging.getLogger(__name__)

EXTRACTOR_VERSION = "1.0.0"
MODEL_VERSION = "nwis-extractor-v1"


def run_extraction(
    document_id: uuid.UUID,
    db: Session,
    llm: Optional[LLMProvider] = None,
) -> ExtractionResult:
    """Run extraction pipeline across all extractors with isolated error handling and validation gate."""
    # 1. Load document
    doc = db.get(Document, document_id)
    if not doc:
        raise DocumentNotFoundError(f"Document {document_id} not found")

    if doc.ocr_status != "done":
        raise DocumentNotOCRedError(
            f"Document {document_id} OCR status is '{doc.ocr_status}', must be 'done' to run extraction"
        )

    # Load normalized text
    text = doc.raw_extracted_text or ""
    if not text and doc.pages:
        text = "\n\n".join(p.text for p in sorted(doc.pages, key=lambda x: x.page_number))

    well_id = doc.well_id

    # 2. Run extractors in isolated try/except blocks
    meta_res: Optional[WellMetadataExtraction] = None
    meta_err: Optional[str] = None
    try:
        meta_res = extract_well_metadata(document_id, text, db=db, llm=llm)
    except Exception as e:
        logger.warning(f"Well metadata extraction failed: {e}")
        meta_err = str(e)

    depth_events_res: list[DepthEventExtraction] = []
    depth_events_err: Optional[str] = None
    try:
        depth_events_res = extract_depth_events(document_id, text, llm=llm)
    except Exception as e:
        logger.warning(f"Depth events extraction failed: {e}")
        depth_events_err = str(e)

    formations_res: list[FormationExtraction] = []
    formations_err: Optional[str] = None
    try:
        formations_res = extract_formations(document_id, text, well_id=well_id, db=db, llm=llm)
    except Exception as e:
        logger.warning(f"Formations extraction failed: {e}")
        formations_err = str(e)

    incidents_res: list[IncidentExtraction] = []
    incidents_err: Optional[str] = None
    try:
        incidents_res = extract_incidents(document_id, text, llm=llm)
    except Exception as e:
        logger.warning(f"Incidents extraction failed: {e}")
        incidents_err = str(e)

    events_created = 0
    needs_review_count = 0

    # 3. Process Well Metadata
    if meta_res is not None:
        ext_record = Extraction(
            extraction_id=uuid.uuid4(),
            doc_id=document_id,
            extractor_name="well_metadata",
            raw_output=meta_res.model_dump(mode="json"),
            status="pending",
        )
        db.add(ext_record)
        db.flush()

        val = validate(ext_record.extraction_id, "well_metadata", meta_res.model_dump(mode="json"), db=db)
        if val.passed:
            ext_record.status = "passed"
            # Update document well_id link if matched
            if not doc.well_id and meta_res.matched_well_id:
                doc.well_id = meta_res.matched_well_id
                well_id = meta_res.matched_well_id
        else:
            ext_record.status = "failed"
            enqueue(ext_record.extraction_id, meta_res.model_dump(mode="json"), val.error or "Schema validation failed", db=db)
            needs_review_count += 1
    elif meta_err:
        ext_record = Extraction(
            extraction_id=uuid.uuid4(),
            doc_id=document_id,
            extractor_name="well_metadata",
            raw_output={"error": meta_err},
            status="failed",
        )
        db.add(ext_record)
        db.flush()
        enqueue(ext_record.extraction_id, {"error": meta_err}, meta_err, db=db)
        needs_review_count += 1

    # 4. Process Depth Events
    if depth_events_res:
        for de in depth_events_res:
            ext_record = Extraction(
                extraction_id=uuid.uuid4(),
                doc_id=document_id,
                extractor_name="depth_events",
                raw_output=de.model_dump(mode="json"),
                status="pending",
            )
            db.add(ext_record)
            db.flush()

            if de.needs_review:
                ext_record.status = "failed"
                enqueue(ext_record.extraction_id, de.model_dump(mode="json"), de.error_reason or "Needs review", db=db)
                needs_review_count += 1
            else:
                val = validate(ext_record.extraction_id, "depth_events", de.model_dump(mode="json"), db=db)
                if val.passed and well_id:
                    ext_record.status = "passed"
                    ev_row = Event(
                        event_id=uuid.uuid4(),
                        well_id=well_id,
                        depth=de.depth,
                        event_type=de.event_type,
                        severity="medium",
                        description_raw=de.description,
                        source_document_id=document_id,
                        confidence_score=de.confidence,
                        extractor_version=EXTRACTOR_VERSION,
                        model_version=MODEL_VERSION,
                        valid_from=datetime.now(timezone.utc),
                    )
                    db.add(ev_row)
                    events_created += 1
                else:
                    ext_record.status = "failed"
                    enqueue(ext_record.extraction_id, de.model_dump(mode="json"), val.error or "Missing well_id", db=db)
                    needs_review_count += 1
    elif depth_events_err:
        ext_record = Extraction(
            extraction_id=uuid.uuid4(),
            doc_id=document_id,
            extractor_name="depth_events",
            raw_output={"error": depth_events_err},
            status="failed",
        )
        db.add(ext_record)
        db.flush()
        enqueue(ext_record.extraction_id, {"error": depth_events_err}, depth_events_err, db=db)
        needs_review_count += 1

    # 5. Process Formations
    if formations_res:
        for form in formations_res:
            ext_record = Extraction(
                extraction_id=uuid.uuid4(),
                doc_id=document_id,
                extractor_name="formations",
                raw_output=form.model_dump(mode="json"),
                status="pending",
            )
            db.add(ext_record)
            db.flush()

            if form.needs_review:
                ext_record.status = "failed"
                enqueue(ext_record.extraction_id, form.model_dump(mode="json"), form.error_reason or "Needs review", db=db)
                needs_review_count += 1
            else:
                val = validate(ext_record.extraction_id, "formations", form.model_dump(mode="json"), db=db)
                if val.passed and well_id:
                    ext_record.status = "passed"
                    interval = WellFormationInterval(
                        interval_id=uuid.uuid4(),
                        well_id=well_id,
                        formation_name=form.formation_name,
                        top_depth=form.top_depth,
                        bottom_depth=form.bottom_depth,
                    )
                    db.add(interval)
                    events_created += 1
                else:
                    ext_record.status = "failed"
                    enqueue(ext_record.extraction_id, form.model_dump(mode="json"), val.error or "Missing well_id", db=db)
                    needs_review_count += 1
    elif formations_err:
        ext_record = Extraction(
            extraction_id=uuid.uuid4(),
            doc_id=document_id,
            extractor_name="formations",
            raw_output={"error": formations_err},
            status="failed",
        )
        db.add(ext_record)
        db.flush()
        enqueue(ext_record.extraction_id, {"error": formations_err}, formations_err, db=db)
        needs_review_count += 1

    # 6. Process Incidents
    if incidents_res:
        for inc in incidents_res:
            ext_record = Extraction(
                extraction_id=uuid.uuid4(),
                doc_id=document_id,
                extractor_name="incidents",
                raw_output=inc.model_dump(mode="json"),
                status="pending",
            )
            db.add(ext_record)
            db.flush()

            val = validate(ext_record.extraction_id, "incidents", inc.model_dump(mode="json"), db=db)
            if val.passed and well_id:
                ext_record.status = "passed"
                depth_val = inc.depth if inc.depth is not None else 1000.0
                ev_row = Event(
                    event_id=uuid.uuid4(),
                    well_id=well_id,
                    depth=depth_val,
                    event_type="incident",
                    severity="high",
                    description_raw=inc.description,
                    mitigation_taken=inc.mitigation,
                    source_document_id=document_id,
                    confidence_score=inc.confidence,
                    extractor_version=EXTRACTOR_VERSION,
                    model_version=MODEL_VERSION,
                    valid_from=datetime.now(timezone.utc),
                )
                db.add(ev_row)
                db.flush()

                inc_row = IncidentEvent(
                    event_id=ev_row.event_id,
                    cause=inc.cause,
                    outcome=inc.outcome,
                    auto_title=inc.auto_title,
                )
                db.add(inc_row)
                events_created += 1
            else:
                ext_record.status = "failed"
                enqueue(ext_record.extraction_id, inc.model_dump(mode="json"), val.error or "Missing well_id", db=db)
                needs_review_count += 1
    elif incidents_err:
        ext_record = Extraction(
            extraction_id=uuid.uuid4(),
            doc_id=document_id,
            extractor_name="incidents",
            raw_output={"error": incidents_err},
            status="failed",
        )
        db.add(ext_record)
        db.flush()
        enqueue(ext_record.extraction_id, {"error": incidents_err}, incidents_err, db=db)
        needs_review_count += 1

    # Total check: if everything failed validation / review queue
    total_processed = events_created + needs_review_count
    if total_processed > 0 and events_created == 0:
        doc.extraction_status = "needs_review"
        status_result = "needs_review"
        event_out = ExtractionNeedsReview(
            payload=ExtractionNeedsReviewPayload(
                document_id=document_id,
                extraction_id=uuid.uuid4(),
                error_detail=f"All {needs_review_count} extractions failed validation and require review",
            )
        )
    else:
        doc.extraction_status = "done"
        status_result = "done"
        event_out = DocumentExtractionCompleted(
            payload=DocumentExtractionCompletedPayload(
                document_id=document_id,
                events_created=events_created,
                needs_review_count=needs_review_count,
            )
        )

    db.commit()

    return ExtractionResult(
        document_id=document_id,
        well_metadata=meta_res,
        depth_events=depth_events_res,
        formations=formations_res,
        incidents=incidents_res,
        events_created=events_created,
        needs_review_count=needs_review_count,
        status=status_result,
    )
