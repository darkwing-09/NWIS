from typing import AsyncIterator, Protocol, runtime_checkable
import uuid

from domain.models.ertmac import WellContext


@runtime_checkable
class ERTMACAdapter(Protocol):
    """
    Contract for real-time well telemetry feeds.
    Both simulator and production adapters implement this exact interface.
    """

    async def get_active_well_context(self, well_id: uuid.UUID) -> WellContext:
        """Fetch current snapshot telemetry for a given active well."""
        ...

    async def subscribe_to_well_events(self, well_id: uuid.UUID) -> AsyncIterator[WellContext]:
        """Stream real-time telemetry events as depth progresses."""
        ...
