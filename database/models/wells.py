import uuid
from datetime import date
from typing import List, Optional
from sqlalchemy import (
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Index,
    String,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.models.base import Base, TimestampMixin

# Use geoalchemy2 if available, or fallback gracefully for non-PostGIS dialects
try:
    from geoalchemy2 import Geography
    GEOGRAPHY_POINT = Geography(geometry_type="POINT", srid=4326)
except ImportError:
    GEOGRAPHY_POINT = String


class Well(Base, TimestampMixin):
    __tablename__ = "wells"

    well_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="active", index=True
    )  # active, completed, abandoned, archived
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    geom = mapped_column(GEOGRAPHY_POINT, nullable=True)

    spud_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    operator_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    field_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    basin_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    formation_intervals: Mapped[List["WellFormationInterval"]] = relationship(
        "WellFormationInterval", back_populates="well", cascade="all, delete-orphan"
    )
    documents: Mapped[List["Document"]] = relationship("Document", back_populates="well")
    events: Mapped[List["Event"]] = relationship("Event", back_populates="well")
    alerts: Mapped[List["Alert"]] = relationship("Alert", back_populates="well")


class WellFormationInterval(Base, TimestampMixin):
    __tablename__ = "well_formation_intervals"

    interval_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    well_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("wells.well_id", ondelete="CASCADE"), nullable=False, index=True
    )
    formation_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    top_depth: Mapped[float] = mapped_column(Float, nullable=False)
    bottom_depth: Mapped[float] = mapped_column(Float, nullable=False)

    well: Mapped["Well"] = relationship("Well", back_populates="formation_intervals")

    __table_args__ = (
        CheckConstraint("top_depth < bottom_depth", name="check_top_depth_less_than_bottom_depth"),
        Index("ix_well_formation_intervals_well_formation", "well_id", "formation_name"),
    )
