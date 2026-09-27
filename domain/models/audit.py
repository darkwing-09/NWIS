import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    log_id: uuid.UUID
    user_id: Optional[uuid.UUID] = None
    action: str
    resource_type: str
    resource_id: uuid.UUID
    timestamp: datetime
    detail: Optional[Dict[str, Any]] = None


class PaginatedAuditLogs(BaseModel):
    items: List[AuditLogRead]
    total: int
    page: int
    page_size: int
