from datetime import datetime, timezone
from typing import List, Optional
import uuid
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.errors import ConflictError, NotFoundError, ValidationError
from database.models.alerts import Alert
from database.models.events import Event
from database.repositories.alerts import AlertRepository
from domain.models.alerts import compute_depth_band, make_dedup_key
from domain.models.risk import RiskAssessmentSchema
from services.audit import service as audit_service

logger = structlog.get_logger("nwis.alerts")


def retrieve_mitigation(
    event_ids: List[uuid.UUID],
    target_depth: float,
    db: Session,
) -> Optional[str]:
    """
    Pull mitigation_taken text from the closest historical matching event.
    RETRIEVAL ONLY: This function contains ZERO LLM calls, by non-negotiable safety design.
    """
    if not event_ids:
        return None

    stmt = (
        select(Event)
        .where(Event.event_id.in_(event_ids))
        .where(Event.mitigation_taken.isnot(None))
        .where(Event.mitigation_taken != "")
    )
    events = list(db.scalars(stmt).all())
    if not events:
        return None

    # Sort by distance between historical event depth and target depth
    events.sort(key=lambda e: abs(e.depth - target_depth))
    return events[0].mitigation_taken


def notify(alert: Alert, db: Session) -> Alert:
    """
    Notifies consumers (e.g. SSE/WebSocket/message queue) and advances status to NOTIFIED.
    """
    logger.info("alert_notified", alert_id=str(alert.alert_id), well_id=str(alert.well_id), status="NOTIFIED")
    alert.status = "NOTIFIED"
    db.flush()
    return alert


def create_alert_if_needed(
    assessment: RiskAssessmentSchema,
    well_id: uuid.UUID,
    current_depth: float,
    db: Session,
) -> Optional[Alert]:
    """
    Entrypoint from Module 16 pipeline:
    1. Suppresses low risk assessments.
    2. Constructs deterministic dedup_key: (well_id, depth_band, event_type).
    3. Suppresses alert if an active alert already exists for the dedup_key.
    4. Retrieves verbatim mitigation from historical events with ZERO LLM generation.
    5. Persists Alert and links evidence.
    6. Dispatches notification and logs audit trail.
    """
    if assessment.risk_level.lower() not in ("medium", "high"):
        logger.debug("alert_skipped_low_risk", risk_level=assessment.risk_level, well_id=str(well_id))
        return None

    event_type = assessment.contributing_evidence.event_type
    depth_band = compute_depth_band(current_depth)
    dedup_key = make_dedup_key(well_id, depth_band, event_type)

    repo = AlertRepository(db)
    existing = repo.find_active_alert_by_dedup_key(dedup_key)
    if existing:
        logger.info(
            "alert_suppressed_duplicate_within_band",
            dedup_key=dedup_key,
            existing_alert_id=str(existing.alert_id),
        )
        return None

    # Retrieve mitigation verbatim from evidence events
    event_ids = assessment.contributing_evidence.event_ids
    recommended_mitigation = retrieve_mitigation(event_ids, target_depth=current_depth, db=db)

    alert = repo.create_alert(
        well_id=well_id,
        risk_level=assessment.risk_level.lower(),
        contributing_wells=assessment.contributing_evidence.contributing_wells,
        dedup_key=dedup_key,
        recommended_mitigation=recommended_mitigation,
        event_ids=event_ids,
        status="CREATED",
    )

    # Transition to NOTIFIED
    notify(alert, db)

    # Record append-only audit event
    try:
        audit_service.log_action(
            user_id=None,
            action="alert_created",
            resource_type="alert",
            resource_id=alert.alert_id,
            detail={
                "well_id": str(well_id),
                "risk_level": alert.risk_level,
                "dedup_key": dedup_key,
                "event_type": event_type,
            },
        )
    except Exception as e:
        logger.warning("audit_log_alert_created_failed", error=str(e))

    return alert


def acknowledge(
    alert_id: uuid.UUID,
    user_id: uuid.UUID,
    db: Session,
) -> Alert:
    """
    Transitions status CREATED/NOTIFIED/ESCALATED -> ACKNOWLEDGED.
    Records acknowledged_by, acknowledged_at, and writes audit log.
    """
    repo = AlertRepository(db)
    alert = repo.get_alert(alert_id)
    if not alert:
        raise NotFoundError("Alert not found", detail={"alert_id": str(alert_id)})

    if alert.status in ("RESOLVED", "EXPIRED"):
        raise ConflictError(
            f"Cannot acknowledge alert in status {alert.status}",
            detail={"alert_id": str(alert_id), "status": alert.status},
        )

    alert.status = "ACKNOWLEDGED"
    alert.acknowledged_by = user_id
    alert.acknowledged_at = datetime.now(timezone.utc)
    db.flush()

    try:
        audit_service.log_action(
            user_id=user_id,
            action="alert_acknowledged",
            resource_type="alert",
            resource_id=alert.alert_id,
            detail={"previous_status": alert.status},
        )
    except Exception as e:
        logger.warning("audit_log_alert_acknowledged_failed", error=str(e))

    return alert


def resolve(
    alert_id: uuid.UUID,
    user_id: uuid.UUID,
    db: Session,
) -> Alert:
    """
    Transitions status ACKNOWLEDGED -> RESOLVED.
    Requires prior acknowledgement.
    """
    repo = AlertRepository(db)
    alert = repo.get_alert(alert_id)
    if not alert:
        raise NotFoundError("Alert not found", detail={"alert_id": str(alert_id)})

    if alert.status != "ACKNOWLEDGED":
        raise ValidationError(
            f"Alert resolution requires prior acknowledgement (current status: {alert.status})",
            detail={"alert_id": str(alert_id), "current_status": alert.status},
        )

    alert.status = "RESOLVED"
    alert.resolved_at = datetime.now(timezone.utc)
    db.flush()

    try:
        audit_service.log_action(
            user_id=user_id,
            action="alert_resolved",
            resource_type="alert",
            resource_id=alert.alert_id,
            detail={"resolved_by": str(user_id)},
        )
    except Exception as e:
        logger.warning("audit_log_alert_resolved_failed", error=str(e))

    return alert


def escalate(
    alert_id: uuid.UUID,
    db: Session,
) -> Alert:
    """
    Transitions status -> ESCALATED if unacknowledged beyond threshold.
    """
    repo = AlertRepository(db)
    alert = repo.get_alert(alert_id)
    if not alert:
        raise NotFoundError("Alert not found", detail={"alert_id": str(alert_id)})

    if alert.status in ("CREATED", "NOTIFIED"):
        alert.status = "ESCALATED"
        db.flush()

        try:
            audit_service.log_action(
                user_id=None,
                action="alert_escalated",
                resource_type="alert",
                resource_id=alert.alert_id,
                detail={"reason": "unacknowledged_timeout"},
            )
        except Exception as e:
            logger.warning("audit_log_alert_escalated_failed", error=str(e))

    return alert


def expire_stale_alerts(cutoff: datetime, db: Session) -> int:
    """Marks CREATED/NOTIFIED alerts older than cutoff as EXPIRED."""
    repo = AlertRepository(db)
    return repo.expire_stale_alerts(cutoff)
