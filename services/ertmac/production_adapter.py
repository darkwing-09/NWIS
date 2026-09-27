from datetime import datetime
from typing import AsyncIterator
import uuid

from domain.models.ertmac import WellContext
from services.ertmac.adapter import ERTMACAdapter


class ProductionERTMACAdapter(ERTMACAdapter):
    """
    Production adapter connecting to Oil India Limited's eRTMAC live rig streaming platform.
    
    STATUS: BLOCKED
    Missing dependency: Confirmed eRTMAC API/WITSML/Kafka stream contract from OIL.
    Per Build Agent Contract Rule 19, this adapter is stubbed raising NotImplementedError
    and tracked as BLOCKED until official interface documentation and test endpoints are provisioned.
    """

    async def get_active_well_context(self, well_id: uuid.UUID) -> WellContext:
        raise NotImplementedError(
            "Blocked pending confirmed eRTMAC integration spec from Oil India Limited -- see docs/blockers.md"
        )

    async def subscribe_to_well_events(self, well_id: uuid.UUID) -> AsyncIterator[WellContext]:
        raise NotImplementedError(
            "Blocked pending confirmed eRTMAC integration spec from Oil India Limited -- see docs/blockers.md"
        )
        if False:
            yield WellContext(well_id=well_id, depth=0.0, timestamp=datetime.now())
