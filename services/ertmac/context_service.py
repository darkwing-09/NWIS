from datetime import datetime, timezone
from typing import Dict, Optional
import uuid
from pydantic import BaseModel

from config.settings import get_settings
from domain.events.schemas import WellContextUpdated, WellContextUpdatedPayload
from domain.logging.logger import get_logger
from domain.models.ertmac import WellContext

logger = get_logger("services.ertmac.context_service")


class HealthStatus(BaseModel):
    status: str  # healthy, unhealthy, degraded
    detail: Optional[str] = None
    last_heartbeat: Optional[datetime] = None


class ContextService:
    """Manages telemetry consumption, staleness validation, and event dispatch."""

    def __init__(
        self,
        staleness_threshold_sec: Optional[int] = None,
        heartbeat_timeout_sec: int = 120,
    ) -> None:
        settings = get_settings()
        self.staleness_threshold_sec = (
            staleness_threshold_sec
            if staleness_threshold_sec is not None
            else settings.ertmac_staleness_threshold_sec
        )
        self.heartbeat_timeout_sec = heartbeat_timeout_sec
        self.last_seen_timestamp: Dict[uuid.UUID, datetime] = {}
        self.active_contexts: Dict[uuid.UUID, WellContext] = {}
        self.last_heartbeat: Optional[datetime] = None

    def record_heartbeat(self, timestamp: Optional[datetime] = None) -> None:
        self.last_heartbeat = timestamp or datetime.now(timezone.utc)

    def handle_context_update(self, context: WellContext) -> Optional[WellContextUpdated]:
        """
        Consumes adapter output, applies staleness/dedup/ordering logic, and returns emitted event if valid.
        1. If context.timestamp <= last_seen_timestamp[context.well_id]: discard (out-of-order or duplicate)
        2. If now() - context.timestamp > staleness_threshold_sec: mark stale, do NOT trigger correlation
        3. Else: update active state, emit WellContextUpdated event
        """
        self.record_heartbeat(context.timestamp)
        now = datetime.now(timezone.utc)

        # 1. Out-of-order / duplicate check
        last_seen = self.last_seen_timestamp.get(context.well_id)
        if last_seen is not None and context.timestamp <= last_seen:
            logger.warning(
                "Discarding out-of-order or duplicate telemetry event",
                well_id=str(context.well_id),
                event_timestamp=str(context.timestamp),
                last_seen=str(last_seen),
            )
            return None

        # Ensure context.timestamp has timezone for comparison
        ts = context.timestamp if context.timestamp.tzinfo else context.timestamp.replace(tzinfo=timezone.utc)
        age_seconds = (now - ts).total_seconds()

        # 2. Staleness check
        if age_seconds > self.staleness_threshold_sec:
            logger.warning(
                "Stale telemetry received; correlation suppressed per safety protocol",
                well_id=str(context.well_id),
                age_seconds=age_seconds,
                threshold=self.staleness_threshold_sec,
            )
            return None

        # 3. Update active state
        self.last_seen_timestamp[context.well_id] = context.timestamp
        self.active_contexts[context.well_id] = context

        event = WellContextUpdated(
            payload=WellContextUpdatedPayload(
                well_id=context.well_id,
                depth=context.depth,
                formation=context.formation,
                timestamp=context.timestamp,
            )
        )
        return event

    def check_adapter_health(self) -> HealthStatus:
        now = datetime.now(timezone.utc)
        if not self.last_heartbeat:
            return HealthStatus(status="unhealthy", detail="No heartbeats received yet")

        hb_time = self.last_heartbeat if self.last_heartbeat.tzinfo else self.last_heartbeat.replace(tzinfo=timezone.utc)
        elapsed = (now - hb_time).total_seconds()
        if elapsed > self.heartbeat_timeout_sec:
            return HealthStatus(
                status="unhealthy",
                detail=f"Missed heartbeats: last heartbeat {elapsed:.1f}s ago exceeds {self.heartbeat_timeout_sec}s timeout",
                last_heartbeat=self.last_heartbeat,
            )

        return HealthStatus(
            status="healthy",
            detail="Feed active and receiving heartbeats",
            last_heartbeat=self.last_heartbeat,
        )


# Global singleton instance for service-wide access
_context_service = ContextService()


def get_context_service() -> ContextService:
    return _context_service
