from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from config.errors import OCRProcessingError
from domain.events.schemas import DocumentUploaded
from domain.logging.logger import get_logger
from services.ocr.service import process_document

logger = get_logger("apps.worker.ocr_worker")


class OCRWorker:
    """Worker processing DocumentUploaded events with bounded retries and DLQ routing."""

    def __init__(self, max_attempts: int = 3) -> None:
        self.max_attempts = max_attempts
        self.attempt_counts: Dict[str, int] = {}
        self.dlq: List[Dict[str, Any]] = []

    def handle_document_uploaded(
        self,
        event: DocumentUploaded,
        db: Optional[Session] = None,
    ) -> bool:
        """
        Process incoming DocumentUploaded event.
        Returns True on successful processing (ACK), False on retry/failure (NACK).
        Routes to DLQ when max_attempts is exceeded.
        """
        doc_id = event.payload.document_id
        doc_key = str(doc_id)
        current_attempts = self.attempt_counts.get(doc_key, 0) + 1
        self.attempt_counts[doc_key] = current_attempts

        try:
            process_document(doc_id, db=db)
            logger.info("Document OCR completed successfully", document_id=doc_key, attempts=current_attempts)
            # Cleanup attempt counter on success
            self.attempt_counts.pop(doc_key, None)
            return True
        except OCRProcessingError as exc:
            logger.warning("Transient OCR error encountered", document_id=doc_key, attempt=current_attempts, error=str(exc))
            if current_attempts >= self.max_attempts:
                logger.error("OCR exceeded max retry attempts, routing to DLQ", document_id=doc_key)
                self.dlq.append({
                    "event": event.model_dump(),
                    "attempts": current_attempts,
                    "error": str(exc),
                })
                self.attempt_counts.pop(doc_key, None)
                return False
            # Nack for retry
            return False
        except Exception as exc:
            logger.error("Unrecoverable error during OCR", document_id=doc_key, error=str(exc))
            self.dlq.append({
                "event": event.model_dump(),
                "attempts": current_attempts,
                "error": str(exc),
            })
            self.attempt_counts.pop(doc_key, None)
            return False
