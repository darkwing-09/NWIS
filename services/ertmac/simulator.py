import asyncio
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, Optional
import uuid

from domain.models.ertmac import WellContext
from services.ertmac.adapter import ERTMACAdapter


class SimulatedERTMACAdapter(ERTMACAdapter):
    """
    Simulated eRTMAC adapter for testing, demonstration, and offline execution.
    Maintains simulated drilling state with manual override controls and tick-based streaming.
    """

    def __init__(self, default_depth: float = 1500.0, default_formation: str = "Barail") -> None:
        self.default_depth = default_depth
        self.default_formation = default_formation
        self._well_states: Dict[uuid.UUID, Dict[str, Any]] = {}

    def _get_or_init_state(self, well_id: uuid.UUID) -> Dict[str, Any]:
        if well_id not in self._well_states:
            self._well_states[well_id] = {
                "depth": self.default_depth,
                "formation": self.default_formation,
                "rop": 12.5,
                "wob": 18.0,
                "rpm": 110.0,
                "spp": 2400.0,
                "mud_weight": 1.15,
                "last_update": datetime.now(timezone.utc),
            }
        return self._well_states[well_id]

    def set_simulated_depth(
        self,
        well_id: uuid.UUID,
        depth: float,
        formation: Optional[str] = None,
    ) -> None:
        """Manual slider / override control for real-time demonstration."""
        state = self._get_or_init_state(well_id)
        state["depth"] = depth
        if formation:
            state["formation"] = formation
        state["last_update"] = datetime.now(timezone.utc)

    async def get_active_well_context(self, well_id: uuid.UUID) -> WellContext:
        """Fetch current simulated depth and telemetry snapshot."""
        state = self._get_or_init_state(well_id)
        return WellContext(
            well_id=well_id,
            depth=state["depth"],
            formation=state["formation"],
            timestamp=state["last_update"],
            bit_depth=state["depth"],
            hole_depth=state["depth"],
            rop=state["rop"],
            wob=state["wob"],
            rpm=state["rpm"],
            spp=state["spp"],
            mud_weight=state["mud_weight"],
        )

    async def subscribe_to_well_events(
        self,
        well_id: uuid.UUID,
        tick_interval_sec: float = 0.05,
        step_m: float = 1.0,
        max_ticks: Optional[int] = None,
    ) -> AsyncIterator[WellContext]:
        """Stream simulated well context with incremental depth progression."""
        state = self._get_or_init_state(well_id)
        ticks = 0

        while True:
            if max_ticks is not None and ticks >= max_ticks:
                break

            now = datetime.now(timezone.utc)
            state["depth"] += step_m
            state["last_update"] = now

            yield WellContext(
                well_id=well_id,
                depth=state["depth"],
                formation=state["formation"],
                timestamp=now,
                bit_depth=state["depth"],
                hole_depth=state["depth"],
                rop=state["rop"],
                wob=state["wob"],
                rpm=state["rpm"],
                spp=state["spp"],
                mud_weight=state["mud_weight"],
            )

            ticks += 1
            if tick_interval_sec > 0:
                await asyncio.sleep(tick_interval_sec)
