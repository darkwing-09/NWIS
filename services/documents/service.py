import uuid
from typing import Optional
from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config.errors import AuthorizationError, NotFoundError, WellNotFoundError
from database.models.documents import Document, Extraction, ExtractionError
from database.models.wells import Well
from domain.events.publisher import publish_event
from domain.events.schemas import DocumentUploaded, DocumentUploadedPayload
from domain.models.auth import AuthenticatedUser
from domain.models.documents import DocumentDetail, DocumentStatus
from infrastructure.storage.client import put_object
from services.audit.service import log_action
from services.documents.validation import validate_upload


def infer_doc_type(filename: Optional[str]) -> str:
    """Infer document type (WCR or DDR) from filename heuristics."""
    if not filename:
        return "WCR"
    lower = filename.lower()
    if "ddr" in lower or "daily" in lower:
        return "DDR"
    return "WCR"


def upload_document(
    file: UploadFile,
    well_id: uuid.UUID,
    user: AuthenticatedUser,
    db: Session,
    amends_document_id: Optional[uuid.UUID] = None,
) -> Document:
    """
    Validate and upload a document, persist record, write object storage, and emit event.
    """
    # 1. Validation (file type, size, virus scan)
    validate_upload(file)

    # 2. Defense in depth: re-verify well existence and field-level RBAC scope
    well = db.scalar(select(Well).where(Well.well_id == well_id))
    if not well:
        raise WellNotFoundError(f"Well with ID {well_id} not found", detail={"well_id": str(well_id)})

    if not user.can_access_field(well.field_name):
        raise AuthorizationError(
            f"User does not have access to well in field '{well.field_name}'",
            detail={"well_id": str(well_id), "field_name": well.field_name},
        )

    # If amends_document_id provided, verify original document exists
    if amends_document_id:
        original = db.scalar(select(Document).where(Document.doc_id == amends_document_id))
        if not original:
            raise NotFoundError(
                f"Original document with ID {amends_document_id} not found",
                detail={"amends_document_id": str(amends_document_id)},
            )

    # 3. Store object in storage
    doc_id = uuid.uuid4()
    object_key = f"{well_id}/{doc_id}.pdf"
    put_object(file, object_key)

    # 4. Create Document record
    doc = Document(
        doc_id=doc_id,
        well_id=well_id,
        file_path=object_key,
        doc_type=infer_doc_type(file.filename),
        ocr_status="pending",
        extraction_status="pending",
        amends_document_id=amends_document_id,
        data_classification="internal",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # 5. Emit DocumentUploaded event
    event = DocumentUploaded(
        payload=DocumentUploadedPayload(
            document_id=doc.doc_id,
            well_id=well_id,
            file_path=object_key,
            uploaded_at=doc.created_at,
        )
    )
    publish_event(event)

    # 6. Audit log write (independent transaction)
    log_action(
        user_id=user.user_id,
        action="document_uploaded",
        resource_type="document",
        resource_id=doc.doc_id,
        detail={"well_id": str(well_id), "doc_type": doc.doc_type, "object_key": object_key},
        engine=db.get_bind(),
    )

    return doc


def get_document(document_id: uuid.UUID, user: AuthenticatedUser, db: Session) -> DocumentDetail:
    """Retrieve document details with field-scoped authorization check."""
    doc = db.scalar(select(Document).where(Document.doc_id == document_id))
    if not doc:
        raise NotFoundError(f"Document with ID {document_id} not found", detail={"doc_id": str(document_id)})

    if doc.well_id:
        well = db.scalar(select(Well).where(Well.well_id == doc.well_id))
        if well and not user.can_access_field(well.field_name):
            raise AuthorizationError(
                f"User does not have access to document in field '{well.field_name}'",
                detail={"doc_id": str(document_id), "field_name": well.field_name},
            )

    return DocumentDetail.model_validate(doc)


def get_document_status(document_id: uuid.UUID, user: AuthenticatedUser, db: Session) -> DocumentStatus:
    """
    Retrieve document processing status with dynamic review count computed from extraction_errors.
    """
    doc = db.scalar(select(Document).where(Document.doc_id == document_id))
    if not doc:
        raise NotFoundError(f"Document with ID {document_id} not found", detail={"doc_id": str(document_id)})

    if doc.well_id:
        well = db.scalar(select(Well).where(Well.well_id == doc.well_id))
        if well and not user.can_access_field(well.field_name):
            raise AuthorizationError(
                f"User does not have access to document in field '{well.field_name}'",
                detail={"doc_id": str(document_id), "field_name": well.field_name},
            )

    # Count extraction errors for this document
    needs_review_count = db.scalar(
        select(func.count(ExtractionError.error_id))
        .join(Extraction, ExtractionError.extraction_id == Extraction.extraction_id)
        .where(Extraction.doc_id == document_id)
    ) or 0

    return DocumentStatus(
        doc_id=doc.doc_id,
        ocr_status=doc.ocr_status,
        extraction_status=doc.extraction_status,
        needs_review_count=needs_review_count,
    )
