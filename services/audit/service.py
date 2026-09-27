import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from sqlalchemy import select, func
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from database.models.audit import AuditLog
from database.session import get_engine
from domain.models.audit import AuditLogRead, PaginatedAuditLogs


def log_action(
    user_id: Optional[uuid.UUID],
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | str,
    detail: Optional[Dict[str, Any]] = None,
    engine: Optional[Engine] = None,
) -> None:
    """Record an append-only audit event in an independent transaction.
    
    deliberate trade-off: an audit write uses its own connection/session so it
    always survives even if the caller's transaction later rolls back.
    """
    target_engine = engine or get_engine()
    r_id = resource_id if isinstance(resource_id, uuid.UUID) else uuid.UUID(str(resource_id))

    with Session(target_engine) as session:
        audit_entry = AuditLog(
            log_id=uuid.uuid4(),
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=r_id,
            timestamp=datetime.now(timezone.utc),
            detail=detail or {},
        )
        session.add(audit_entry)
        session.commit()


def query_logs(
    db: Session,
    resource_type: Optional[str] = None,
    resource_id: Optional[uuid.UUID | str] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    page: int = 1,
    page_size: int = 50,
) -> PaginatedAuditLogs:
    """Query audit logs with filtering and pagination."""
    query = select(AuditLog)
    count_query = select(func.count(AuditLog.log_id))

    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)
        count_query = count_query.where(AuditLog.resource_type == resource_type)

    if resource_id:
        r_id = resource_id if isinstance(resource_id, uuid.UUID) else uuid.UUID(str(resource_id))
        query = query.where(AuditLog.resource_id == r_id)
        count_query = count_query.where(AuditLog.resource_id == r_id)

    if date_from:
        query = query.where(AuditLog.timestamp >= date_from)
        count_query = count_query.where(AuditLog.timestamp >= date_from)

    if date_to:
        query = query.where(AuditLog.timestamp <= date_to)
        count_query = count_query.where(AuditLog.timestamp <= date_to)

    total = db.scalar(count_query) or 0

    offset = max(0, (page - 1) * page_size)
    query = query.order_by(AuditLog.timestamp.desc()).offset(offset).limit(page_size)

    results = db.scalars(query).all()
    items = [AuditLogRead.model_validate(r) for r in results]

    return PaginatedAuditLogs(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )
