from datetime import datetime
from typing import List, Optional
import uuid
from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from database.models.alerts import Alert, AlertEvidence

ACTIVE_STATUSES = ("CREATED", "NOTIFIED", "ACKNOWLEDGED", "ESCALATED")


class AlertRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def find_active_alert_by_dedup_key(self, dedup_key: str) -> Optional[Alert]:
        stmt = (
            select(Alert)
            .where(Alert.dedup_key == dedup_key)
            .where(Alert.status.in_(ACTIVE_STATUSES))
            .options(selectinload(Alert.evidence))
        )
        return self.db.scalars(stmt).first()

    def get_alert(self, alert_id: uuid.UUID) -> Optional[Alert]:
        stmt = (
            select(Alert)
            .where(Alert.alert_id == alert_id)
            .options(selectinload(Alert.evidence))
        )
        return self.db.scalars(stmt).first()

    def create_alert(
        self,
        well_id: uuid.UUID,
        risk_level: str,
        contributing_wells: List[str],
        dedup_key: str,
        recommended_mitigation: Optional[str],
        event_ids: List[uuid.UUID],
        status: str = "CREATED",
    ) -> Alert:
        alert = Alert(
            alert_id=uuid.uuid4(),
            well_id=well_id,
            risk_level=risk_level,
            status=status,
            contributing_wells=contributing_wells,
            dedup_key=dedup_key,
            recommended_mitigation=recommended_mitigation,
        )
        self.db.add(alert)
        self.db.flush()

        for eid in event_ids:
            ev = AlertEvidence(alert_id=alert.alert_id, event_id=eid)
            self.db.add(ev)

        self.db.flush()
        return alert

    def list_alerts(
        self,
        well_id: Optional[uuid.UUID] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Alert]:
        stmt = select(Alert).options(selectinload(Alert.evidence)).order_by(Alert.created_at.desc())
        if well_id:
            stmt = stmt.where(Alert.well_id == well_id)
        if status:
            stmt = stmt.where(Alert.status == status)
        stmt = stmt.limit(limit).offset(offset)
        return list(self.db.scalars(stmt).all())

    def expire_stale_alerts(self, cutoff: datetime) -> int:
        stmt = (
            update(Alert)
            .where(Alert.status.in_(("CREATED", "NOTIFIED")))
            .where(Alert.created_at < cutoff)
            .values(status="EXPIRED")
        )
        res = self.db.execute(stmt)
        self.db.flush()
        return res.rowcount
