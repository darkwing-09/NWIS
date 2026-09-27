from datetime import datetime, timedelta, timezone
import uuid
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database.models.alerts import Alert
from database.models.base import Base
from database.models.jobs import IntegrationEvent
from domain.models.ertmac import WellContext
from services.alerts import service as alert_service
from services.ertmac.context_service import ContextService
from services.ertmac.simulator import SimulatedERTMACAdapter
from services.realtime.pipeline import RealtimePipelineOrchestrator, process_telemetry_tick
from tests.fixtures.synthetic_wells import generate_synthetic_dataset


@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_realtime_pipeline_end_to_end_with_simulator(test_db):
    """
    End-to-End Real-Time Pipeline Acceptance Test:
    1. Populate database with synthetic wells having planted overlap incidents in Barail at 2450m.
    2. Stream telemetry ticks from SimulatedERTMACAdapter.
    3. Pipeline automatically processes context -> runs correlation -> assesses risk -> creates alert.
    4. Verifies verbatim mitigation retrieval, dedup suppression, lifecycle transitions, and audit logs.
    """
    # Step 1: Seed synthetic dataset
    dataset = generate_synthetic_dataset(num_wells=6, planted_overlaps=3)
    dataset.populate_db(test_db)
    active_well_id = dataset.target_well_id

    # Create isolated ContextService
    context_service = ContextService(staleness_threshold_sec=300)
    orchestrator = RealtimePipelineOrchestrator(context_service=context_service)

    # Step 2: Initialize simulator starting at 2440m (approaching the 2450m Barail sand)
    adapter = SimulatedERTMACAdapter(default_depth=2440.0, default_formation="Barail")
    adapter.set_simulated_depth(active_well_id, depth=2440.0, formation="Barail")

    # Process first tick at 2440m (within 2450m ± 25m correlation depth window)
    tick1 = WellContext(
        well_id=active_well_id,
        depth=2440.0,
        formation="Barail",
        timestamp=datetime.now(timezone.utc),
    )
    result1 = orchestrator.process_tick(tick1, db=test_db)

    assert result1.status == "processed"
    assert result1.depth == 2440.0
    assert len(result1.correlation_results) >= 1
    # Multiple offset wells had loss_circulation in this Barail sand window -> high risk
    assert len(result1.risk_assessments) >= 1
    high_risks = [r for r in result1.risk_assessments if r.risk_level in ("medium", "high")]
    assert len(high_risks) >= 1

    # Alert must be created and transitioned to NOTIFIED
    assert len(result1.alerts_created) == 1
    alert_created = result1.alerts_created[0]
    assert alert_created.status == "NOTIFIED"
    assert alert_created.well_id == active_well_id
    assert alert_created.recommended_mitigation is not None
    # Verbatim mitigation verification
    assert "LCM pill" in alert_created.recommended_mitigation or "reduced pump rate" in alert_created.recommended_mitigation

    # Step 3: Process second tick at 2445m (same depth band 2400-2450m or 2450m band)
    tick2 = WellContext(
        well_id=active_well_id,
        depth=2445.0,
        formation="Barail",
        timestamp=datetime.now(timezone.utc) + timedelta(seconds=1),
    )
    result2 = orchestrator.process_tick(tick2, db=test_db)
    assert result2.status == "processed"
    # Duplicate alert within the same depth band must be SUPPRESSED
    assert len(result2.alerts_created) == 0

    # Step 4: Verify durable audit history in integration_events table
    events = list(test_db.scalars(select(IntegrationEvent).where(IntegrationEvent.well_id == active_well_id)).all())
    assert len(events) == 2
    assert events[0].depth == 2440.0
    assert events[1].depth == 2445.0

    # Step 5: Engineer acknowledges and resolves the alert
    user_id = uuid.uuid4()
    ack_alert = alert_service.acknowledge(alert_created.alert_id, user_id=user_id, db=test_db)
    assert ack_alert.status == "ACKNOWLEDGED"
    assert ack_alert.acknowledged_by == user_id

    resolved_alert = alert_service.resolve(alert_created.alert_id, user_id=user_id, db=test_db)
    assert resolved_alert.status == "RESOLVED"
    assert resolved_alert.resolved_at is not None


def test_realtime_pipeline_rejects_stale_telemetry(test_db):
    """Verifies that the pipeline suppresses telemetry older than staleness threshold."""
    well_id = uuid.uuid4()
    context_service = ContextService(staleness_threshold_sec=30)
    orchestrator = RealtimePipelineOrchestrator(context_service=context_service)

    stale_time = datetime.now(timezone.utc) - timedelta(seconds=120)
    stale_tick = WellContext(
        well_id=well_id,
        depth=2500.0,
        formation="Barail",
        timestamp=stale_time,
    )

    result = orchestrator.process_tick(stale_tick, db=test_db)
    assert result.status == "suppressed"
    assert result.reason == "stale_or_out_of_order"
    assert len(result.alerts_created) == 0


def test_realtime_pipeline_rejects_out_of_order_telemetry(test_db):
    """Verifies that backwards or duplicate timestamps are discarded."""
    well_id = uuid.uuid4()
    context_service = ContextService(staleness_threshold_sec=300)
    orchestrator = RealtimePipelineOrchestrator(context_service=context_service)

    t1 = datetime.now(timezone.utc)
    t2 = t1 - timedelta(seconds=10)  # Earlier than t1

    tick1 = WellContext(well_id=well_id, depth=2100.0, formation="Tipam", timestamp=t1)
    tick2 = WellContext(well_id=well_id, depth=2090.0, formation="Tipam", timestamp=t2)

    res1 = orchestrator.process_tick(tick1, db=test_db)
    res2 = orchestrator.process_tick(tick2, db=test_db)

    # Note: tick1 might fail correlation if well not in DB, but status check verifies handle_context_update
    assert res2.status == "suppressed"
    assert res2.reason == "stale_or_out_of_order"
