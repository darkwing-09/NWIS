import asyncio
from datetime import datetime, timedelta, timezone
import uuid
import pytest

from domain.models.ertmac import WellContext
from services.ertmac.context_service import ContextService
from services.ertmac.production_adapter import ProductionERTMACAdapter
from services.ertmac.simulator import SimulatedERTMACAdapter


@pytest.mark.asyncio
async def test_simulator_yields_incrementing_depth():
    adapter = SimulatedERTMACAdapter(default_depth=1500.0)
    well_id = uuid.uuid4()

    depths = []
    async for ctx in adapter.subscribe_to_well_events(well_id, tick_interval_sec=0.001, step_m=2.0, max_ticks=3):
        depths.append(ctx.depth)

    assert len(depths) == 3
    assert depths == [1502.0, 1504.0, 1506.0]


@pytest.mark.asyncio
async def test_simulator_respects_manual_override():
    adapter = SimulatedERTMACAdapter(default_depth=1500.0)
    well_id = uuid.uuid4()

    # Apply manual slider override
    adapter.set_simulated_depth(well_id, depth=2750.5, formation="Kopili")
    ctx = await adapter.get_active_well_context(well_id)

    assert ctx.depth == 2750.5
    assert ctx.formation == "Kopili"


@pytest.mark.asyncio
async def test_production_adapter_raises_not_implemented_blocked():
    prod_adapter = ProductionERTMACAdapter()
    well_id = uuid.uuid4()

    with pytest.raises(NotImplementedError) as exc_info:
        await prod_adapter.get_active_well_context(well_id)
    assert "Blocked pending confirmed eRTMAC integration spec" in str(exc_info.value)

    with pytest.raises(NotImplementedError):
        async for _ in prod_adapter.subscribe_to_well_events(well_id):
            pass


def test_discards_out_of_order_event():
    service = ContextService(staleness_threshold_sec=60)
    well_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    # First event
    ctx1 = WellContext(well_id=well_id, depth=1000.0, timestamp=now)
    event1 = service.handle_context_update(ctx1)
    assert event1 is not None

    # Out-of-order event with earlier timestamp
    ctx2 = WellContext(well_id=well_id, depth=999.0, timestamp=now - timedelta(seconds=5))
    event2 = service.handle_context_update(ctx2)
    assert event2 is None  # Discarded


def test_discards_duplicate_timestamp():
    service = ContextService(staleness_threshold_sec=60)
    well_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    ctx1 = WellContext(well_id=well_id, depth=1000.0, timestamp=now)
    event1 = service.handle_context_update(ctx1)
    assert event1 is not None

    # Duplicate timestamp
    ctx2 = WellContext(well_id=well_id, depth=1001.0, timestamp=now)
    event2 = service.handle_context_update(ctx2)
    assert event2 is None  # Discarded


def test_stale_context_does_not_trigger_correlation():
    service = ContextService(staleness_threshold_sec=30)
    well_id = uuid.uuid4()
    stale_time = datetime.now(timezone.utc) - timedelta(seconds=45)

    ctx = WellContext(well_id=well_id, depth=2000.0, timestamp=stale_time)
    event = service.handle_context_update(ctx)
    assert event is None  # Stale data suppressed


def test_fresh_context_triggers_pipeline():
    service = ContextService(staleness_threshold_sec=60)
    well_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    ctx = WellContext(well_id=well_id, depth=2100.0, formation="Barail", timestamp=now)
    event = service.handle_context_update(ctx)

    assert event is not None
    assert event.event_type == "WellContextUpdated"
    assert event.payload.well_id == well_id
    assert event.payload.depth == 2100.0
    assert event.payload.formation == "Barail"


def test_reports_unhealthy_after_missed_heartbeats():
    service = ContextService(heartbeat_timeout_sec=5)

    # Initial state: no heartbeats
    h0 = service.check_adapter_health()
    assert h0.status == "unhealthy"

    # Send heartbeat
    now = datetime.now(timezone.utc)
    service.record_heartbeat(now)
    h1 = service.check_adapter_health()
    assert h1.status == "healthy"

    # Expired heartbeat
    service.record_heartbeat(now - timedelta(seconds=10))
    h2 = service.check_adapter_health()
    assert h2.status == "unhealthy"
    assert "Missed heartbeats" in h2.detail
