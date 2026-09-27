import uuid
from typing import Optional
from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from sqlalchemy.orm import Session

from apps.api.middleware.rbac import get_current_user
from database.session import get_session
from domain.models.auth import AuthenticatedUser
from domain.models.documents import DocumentDetail, DocumentStatus, DocumentUploadResponse
from services.auth.rbac import require_permission
from services.documents.service import get_document, get_document_status, upload_document

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post(
    "/upload",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_permission("document", "upload"))],
)
def upload_document_endpoint(
    file: UploadFile = File(...),
    well_id: uuid.UUID = Form(...),
    amends_document_id: Optional[uuid.UUID] = Form(None),
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> DocumentUploadResponse:
    """Upload a well document (WCR or DDR) for processing."""
    doc = upload_document(
        file=file,
        well_id=well_id,
        user=user,
        db=db,
        amends_document_id=amends_document_id,
    )
    return DocumentUploadResponse(doc_id=doc.doc_id, status=doc.ocr_status)


@router.get(
    "/{document_id}",
    response_model=DocumentDetail,
    dependencies=[Depends(require_permission("document", "read"))],
)
def get_document_endpoint(
    document_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> DocumentDetail:
    """Get metadata and status details for a document."""
    return get_document(document_id=document_id, user=user, db=db)


@router.get(
    "/{document_id}/status",
    response_model=DocumentStatus,
    dependencies=[Depends(require_permission("document", "read"))],
)
def get_document_status_endpoint(
    document_id: uuid.UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> DocumentStatus:
    """Poll processing status for a document."""
    return get_document_status(document_id=document_id, user=user, db=db)
