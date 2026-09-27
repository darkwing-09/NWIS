import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    JSON,
    String,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.models.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    log_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    resource_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True
    )
    detail: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)

    user: Mapped[Optional["User"]] = relationship("User", back_populates="audit_logs")  # type: ignore[name-defined]

    __table_args__ = (
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
    )


from sqlalchemy import event  # noqa: E402


@event.listens_for(AuditLog, "before_update")
def receive_before_update(mapper, connection, target):
    raise PermissionError("audit_logs is append-only: UPDATE is strictly prohibited")


@event.listens_for(AuditLog, "before_delete")
def receive_before_delete(mapper, connection, target):
    raise PermissionError("audit_logs is append-only: DELETE is strictly prohibited")
