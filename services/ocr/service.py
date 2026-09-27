import tempfile
import uuid
from pathlib import Path
from typing import Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.errors import CorruptFileError, NotFoundError, OCRProcessingError
from database.models.documents import Document, DocumentPage
from database.session import get_worker_session
from domain.events.publisher import publish_event
from domain.events.schemas import DocumentOCRCompleted, DocumentOCRCompletedPayload
from domain.logging.logger import get_logger
from domain.models.ocr import OCRPageResult, OCRResult
from infrastructure.storage.client import get_object
from services.ocr.classifier import classify_document
from services.ocr.confidence import calculate_document_confidence
from services.ocr.native_extractor import extract_native_text
from services.ocr.normalizer import normalize_units, reconstruct_pages, strip_headers_footers
from services.ocr.ocr_engine import ocr_pages
from services.ocr.text_layer import detect_text_layer

logger = get_logger("services.ocr.service")


def process_document(document_id: uuid.UUID, db: Optional[Session] = None) -> OCRResult:
    """
    Orchestrate the complete OCR pipeline for a document.
    Enforces that digital text-layer pages bypass OCR, computes confidence,
    normalizes headers/footers and units, persists results, and emits DocumentOCRCompleted.
    """
    owns_session = False
    if db is None:
        db = get_worker_session()
        owns_session = True

    try:
        doc = db.scalar(select(Document).where(Document.doc_id == document_id))
        if not doc:
            raise NotFoundError(f"Document with ID {document_id} not found", detail={"document_id": str(document_id)})

        # Download document bytes from object storage
        try:
            pdf_bytes = get_object(doc.file_path)
        except Exception as exc:
            doc.ocr_status = "failed"
            db.commit()
            raise NotFoundError(f"Document object file missing from storage: {exc}", detail={"file_path": doc.file_path}) from exc

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_file:
            tmp_path = Path(tmp_file.name)
            tmp_file.write(pdf_bytes)

        try:
            # 1. Classification
            doc_class = classify_document(tmp_path)
            doc.doc_type = doc_class.doc_type

            # 2. Text-layer detection
            text_layer_res = detect_text_layer(tmp_path)
            page_flags = text_layer_res.page_flags

            native_indices = [i for i, has_text in enumerate(page_flags) if has_text]
            scanned_indices = [i for i, has_text in enumerate(page_flags) if not has_text]

            native_results: Dict[int, str] = {}
            ocr_results: Dict[int, OCRPageResult] = {}

            # 3. Native extraction for text-layer pages
            if native_indices:
                native_results = extract_native_text(tmp_path, native_indices)

            # 4. OCR processing strictly on scanned pages lacking text layer
            if scanned_indices:
                ocr_results = ocr_pages(tmp_path, scanned_indices)

            # 5. Page-level confidences
            page_confidences: Dict[int, float] = {}
            for idx in native_indices:
                page_confidences[idx] = 1.0  # Native digital text has 1.0 confidence
            for idx in scanned_indices:
                if idx in ocr_results:
                    page_confidences[idx] = ocr_results[idx].confidence
                else:
                    page_confidences[idx] = 0.0

            # 6. Page reconstruction, header/footer removal, unit normalization
            raw_pages = reconstruct_pages(native_results, ocr_results, total_pages=len(page_flags))
            cleaned_pages = strip_headers_footers(raw_pages)
            normalized_pages = [normalize_units(p) for p in cleaned_pages]

            page_texts_dict = {i: text for i, text in enumerate(normalized_pages)}
            doc_confidence, low_confidence_pages = calculate_document_confidence(page_confidences, page_texts_dict)

            full_text = "\n\n--- PAGE BREAK ---\n\n".join(normalized_pages)

            # 7. Persistence
            doc.raw_extracted_text = full_text
            doc.confidence = doc_confidence
            doc.ocr_status = "done"

            # Persist per-page records
            for idx, page_text in enumerate(normalized_pages):
                page_row = DocumentPage(
                    page_id=uuid.uuid4(),
                    doc_id=doc.doc_id,
                    page_number=idx + 1,
                    text=page_text,
                    confidence=page_confidences.get(idx, 1.0),
                    has_text_layer=page_flags[idx],
                )
                db.add(page_row)

            db.commit()
            db.refresh(doc)

            # 8. Event emission
            event = DocumentOCRCompleted(
                payload=DocumentOCRCompletedPayload(
                    document_id=doc.doc_id,
                    confidence=doc_confidence,
                    low_confidence_pages=low_confidence_pages,
                )
            )
            publish_event(event)

            return OCRResult(
                document_id=doc.doc_id,
                confidence=doc_confidence,
                text=full_text,
                low_confidence_pages=low_confidence_pages,
            )

        except Exception as exc:
            db.rollback()
            # Mark document failed so it never hangs in pending status
            doc.ocr_status = "failed"
            db.commit()
            logger.error("OCR pipeline failure", document_id=str(document_id), error=str(exc))
            raise
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    finally:
        if owns_session:
            db.close()
