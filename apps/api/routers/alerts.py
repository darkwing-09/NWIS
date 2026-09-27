from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from config.errors import NotFoundError
from database.repositories.alerts import AlertRepository
from database.session import get_db
from domain.models.alerts import AlertSchema
from domain.models.auth import AuthenticatedUser
from services.alerts import service as alert_service
from services.auth.rbac import require_permission

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=List[AlertSchema])
def list_alerts(
    well_id: Optional[uuid.UUID] = Query(None, description="Filter by well ID"),
    status: Optional[str] = Query(None, description="Filter by alert status"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: AuthenticatedUser = Depends(require_permission("alerts:read")),
    db: Session = Depends(get_db),
) -> List[AlertSchema]:
    """List alerts with optional status and well filtering."""
    repo = AlertRepository(db)
    alerts = repo.list_alerts(well_id=well_id, status=status, limit=limit, offset=offset)
    return [AlertSchema.model_validate(a) for a in alerts]


@router.get("/{alert_id}", response_model=AlertSchema)
def get_alert_detail(
    alert_id: uuid.UUID,
    user: AuthenticatedUser = Depends(require_permission("alerts:read")),
    db: Session = Depends(get_db),
) -> AlertSchema:
    """Get single alert detail."""
    repo = AlertRepository(db)
    alert = repo.get_alert(alert_id)
    if not alert:
        raise NotFoundError("Alert not found", detail={"alert_id": str(alert_id)})
    return AlertSchema.model_validate(alert)


@router.post("/{alert_id}/acknowledge", response_model=AlertSchema)
def acknowledge_alert(
    alert_id: uuid.UUID,
    user: AuthenticatedUser = Depends(require_permission("alerts:write")),
    db: Session = Depends(get_db),
) -> AlertSchema:
    """Acknowledge an active alert (status -> ACKNOWLEDGED)."""
    alert = alert_service.acknowledge(alert_id=alert_id, user_id=user.user_id, db=db)
    return AlertSchema.model_validate(alert)


@router.post("/{alert_id}/resolve", response_model=AlertSchema)
def resolve_alert(
    alert_id: uuid.UUID,
    user: AuthenticatedUser = Depends(require_permission("alerts:write")),
    db: Session = Depends(get_db),
) -> AlertSchema:
    """Resolve an acknowledged alert (status -> RESOLVED). Requires prior acknowledgement."""
    alert = alert_service.resolve(alert_id=alert_id, user_id=user.user_id, db=db)
    return AlertSchema.model_validate(alert)
