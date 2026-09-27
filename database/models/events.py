import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.models.base import Base, TimestampMixin


class Event(Base, TimestampMixin):
    __tablename__ = "events"

    event_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    well_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("wells.well_id", ondelete="CASCADE"), nullable=False, index=True
    )
    depth: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(50), nullable=False, default="medium")
    description_raw: Mapped[str] = mapped_column(Text, nullable=False)
    mitigation_taken: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("documents.doc_id", ondelete="SET NULL"), nullable=True, index=True
    )
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    extractor_version: Mapped[str] = mapped_column(String(100), nullable=False, default="1.0.0")
    model_version: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    superseded_by_event_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("events.event_id", ondelete="SET NULL"), nullable=True
    )
    valid_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    duplicate_group_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True, index=True
    )

    well: Mapped["Well"] = relationship("Well", back_populates="events")  # type: ignore[name-defined]
    incident: Mapped[Optional["IncidentEvent"]] = relationship("IncidentEvent", back_populates="event", uselist=False)

    __table_args__ = (
        Index("ix_events_well_depth", "well_id", "depth"),
    )


class IncidentEvent(Base):
    __tablename__ = "incident_events"

    event_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("events.event_id", ondelete="CASCADE"), primary_key=True
    )
    cause: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    outcome: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    auto_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    event: Mapped["Event"] = relationship("Event", back_populates="incident")


class DrillingParameter(Base, TimestampMixin):
    __tablename__ = "drilling_parameters"

    param_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    well_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("wells.well_id", ondelete="CASCADE"), nullable=False, index=True
    )
    depth: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    mud_weight: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    casing_size: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
