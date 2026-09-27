from datetime import datetime, timezone
from typing import List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
import structlog

from database.models.jobs import IntegrationEvent
from domain.models.alerts import AlertSchema
from domain.models.correlation import CorrelationResult
from domain.models.ertmac import WellContext
from domain.models.risk import RiskAssessmentSchema
from services.alerts.service import create_alert_if_needed
from services.correlation.engine import run_correlation
from services.ertmac.context_service import ContextService, get_context_service
from services.ertmac.simulator import SimulatedERTMACAdapter
from services.risk.service import assess_risk

logger = structlog.get_logger("nwis.realtime.pipeline")


class PipelineRunResult(BaseModel):
    well_id: uuid.UUID
    depth: float
    formation: str
    status: str  # "processed", "suppressed", "error"
    reason: Optional[str] = None
    correlation_results: List[CorrelationResult] = Field(default_factory=list)
    risk_assessments: List[RiskAssessmentSchema] = Field(default_factory=list)
    alerts_created: List[AlertSchema] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(from_attributes=True)


def process_telemetry_tick(
    context: WellContext,
    db: Session,
    context_service: Optional[ContextService] = None,
) -> PipelineRunResult:
    """
    Executes continuous real-time intelligence pipeline for a single telemetry tick:
    1. Validates context freshness, ordering, and deduplication via ContextService.
    2. Persists durable log entry to integration_events table.
    3. Runs deterministic cross-well correlation against nearby offset wells.
    4. Evaluates risk with Stage 1 rule thresholds and defensive safety floor.
    5. Dispatches alerts with verbatim historical mitigations and suppression.
    """
    svc = context_service or get_context_service()

    # Step 1: Validate context (discards out-of-order, duplicates, and stale ticks)
    event = svc.handle_context_update(context)
    if not event:
        logger.info(
            "telemetry_tick_suppressed",
            well_id=str(context.well_id),
            depth=context.depth,
        )
        return PipelineRunResult(
            well_id=context.well_id,
            depth=context.depth,
            formation=context.formation,
            status="suppressed",
            reason="stale_or_out_of_order",
        )

    # Step 2: Persist durable log entry in integration_events
    try:
        integration_event = IntegrationEvent(
            event_id=uuid.uuid4(),
            well_id=context.well_id,
            depth=context.depth,
            formation=context.formation,
            timestamp=context.timestamp,
        )
        db.add(integration_event)
        db.flush()
    except Exception as e:
        logger.warning("integration_event_persist_failed", error=str(e))

    # Step 3: Run deterministic cross-well correlation
    try:
        correlation_results = run_correlation(
            well_id=context.well_id,
            depth=context.depth,
            formation=context.formation,
            db=db,
        )
    except Exception as e:
        logger.warning("cross_well_correlation_failed", well_id=str(context.well_id), error=str(e))
        return PipelineRunResult(
            well_id=context.well_id,
            depth=context.depth,
            formation=context.formation,
            status="error",
            reason=str(e),
        )

    # Step 4: Assess risk
    risk_assessments = assess_risk(
        correlation_results=correlation_results,
        well_id=context.well_id,
    )

    # Step 5: Evaluate alert generation and mitigation retrieval
    alerts_created: List[AlertSchema] = []
    for assessment in risk_assessments:
        alert = create_alert_if_needed(
            assessment=assessment,
            well_id=context.well_id,
            current_depth=context.depth,
            db=db,
        )
        if alert:
            alerts_created.append(AlertSchema.model_validate(alert))

    logger.info(
        "telemetry_tick_processed",
        well_id=str(context.well_id),
        depth=context.depth,
        correlations_found=len(correlation_results),
        risk_assessments=len(risk_assessments),
        alerts_created=len(alerts_created),
    )

    return PipelineRunResult(
        well_id=context.well_id,
        depth=context.depth,
        formation=context.formation,
        status="processed",
        correlation_results=correlation_results,
        risk_assessments=risk_assessments,
        alerts_created=alerts_created,
    )


class RealtimePipelineOrchestrator:
    def __init__(self, context_service: Optional[ContextService] = None) -> None:
        self.context_service = context_service or get_context_service()

    def process_tick(self, context: WellContext, db: Session) -> PipelineRunResult:
        return process_telemetry_tick(context=context, db=db, context_service=self.context_service)

    def run_simulation(
        self,
        adapter: SimulatedERTMACAdapter,
        db: Session,
        max_ticks: int = 50,
    ) -> List[PipelineRunResult]:
        """
        Runs a simulation stream from adapter up to max_ticks, processing each tick sequentially.
        """
        results: List[PipelineRunResult] = []
        count = 0
        for ctx in adapter.stream_events():
            res = self.process_tick(ctx, db=db)
            results.append(res)
            count += 1
            if count >= max_ticks:
                break
        return results
