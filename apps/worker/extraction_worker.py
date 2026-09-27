from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from config.errors import NWISError
from database.session import get_worker_session
from domain.events.schemas import DocumentOCRCompleted
from domain.logging.logger import get_logger
from services.extraction.orchestrator import run_extraction

logger = get_logger("apps.worker.extraction_worker")


class ExtractionWorker:
    """Worker processing DocumentOCRCompleted events with bounded retries and DLQ routing."""

    def __init__(self, max_attempts: int = 3) -> None:
        self.max_attempts = max_attempts
        self.attempt_counts: Dict[str, int] = {}
        self.dlq: List[Dict[str, Any]] = []

    def handle_document_ocr_completed(
        self,
        event: DocumentOCRCompleted,
        db: Optional[Session] = None,
    ) -> bool:
        """
        Process incoming DocumentOCRCompleted event.
        Returns True on successful processing (ACK), False on retry/failure (NACK).
        Routes to DLQ when max_attempts is exceeded.
        """
        doc_id = event.payload.document_id
        doc_key = str(doc_id)
        current_attempts = self.attempt_counts.get(doc_key, 0) + 1
        self.attempt_counts[doc_key] = current_attempts

        session_ctx = None
        if db is None:
            session_ctx = get_worker_session()
            session = session_ctx.__enter__()
        else:
            session = db

        try:
            res = run_extraction(doc_id, db=session)
            logger.info(
                "Document extraction completed",
                document_id=doc_key,
                status=res.status,
                events_created=res.events_created,
                needs_review_count=res.needs_review_count,
            )
            self.attempt_counts.pop(doc_key, None)
            return True
        except NWISError as exc:
            logger.warning(
                "Domain error during extraction",
                document_id=doc_key,
                attempt=current_attempts,
                error=str(exc),
            )
            if current_attempts >= self.max_attempts:
                logger.error("Extraction exceeded max retries, routing to DLQ", document_id=doc_key)
                self.dlq.append({
                    "event": event.model_dump(),
                    "attempts": current_attempts,
                    "error": str(exc),
                })
                self.attempt_counts.pop(doc_key, None)
                return False
            return False
        except Exception as exc:
            logger.error("Unrecoverable error during extraction", document_id=doc_key, error=str(exc))
            self.dlq.append({
                "event": event.model_dump(),
                "attempts": current_attempts,
                "error": str(exc),
            })
            self.attempt_counts.pop(doc_key, None)
            return False
        finally:
            if session_ctx is not None:
                session_ctx.__exit__(None, None, None)
