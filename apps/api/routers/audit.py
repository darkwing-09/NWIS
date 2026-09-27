import uuid
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database.session import get_session
from domain.models.audit import PaginatedAuditLogs
from services.auth.rbac import require_permission
from services.audit.service import query_logs

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/logs", response_model=PaginatedAuditLogs, dependencies=[Depends(require_permission("audit:read"))])
def get_audit_logs(
    resource_type: Optional[str] = Query(None, description="Filter by resource type"),
    resource_id: Optional[uuid.UUID] = Query(None, description="Filter by resource ID"),
    date_from: Optional[datetime] = Query(None, description="Filter by start date"),
    date_to: Optional[datetime] = Query(None, description="Filter by end date"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
    db: Session = Depends(get_session),
) -> PaginatedAuditLogs:
    """Query append-only audit trail logs with filtering and pagination."""
    return query_logs(
        db=db,
        resource_type=resource_type,
        resource_id=resource_id,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )
