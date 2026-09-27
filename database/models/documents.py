import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.models.base import Base, TimestampMixin

from sqlalchemy.types import TypeDecorator

class PortableVector(TypeDecorator):
    """pgvector Vector(1536) on PostgreSQL, JSON fallback on SQLite/other dialects."""
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            try:
                from pgvector.sqlalchemy import Vector
                return dialect.type_descriptor(Vector(1536))
            except ImportError:
                return dialect.type_descriptor(JSON())
        return dialect.type_descriptor(JSON())


EMBEDDING_COLUMN = PortableVector()


class Document(Base, TimestampMixin):
    __tablename__ = "documents"

    doc_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    well_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("wells.well_id", ondelete="SET NULL"), nullable=True, index=True
    )
    doc_type: Mapped[str] = mapped_column(String(50), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    ocr_status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending", index=True)
    extraction_status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending", index=True)
    raw_extracted_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    amends_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("documents.doc_id"), nullable=True
    )
    retention_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    data_classification: Mapped[str] = mapped_column(String(50), nullable=False, default="internal")

    well: Mapped[Optional["Well"]] = relationship("Well", back_populates="documents")  # type: ignore[name-defined]
    pages: Mapped[List["DocumentPage"]] = relationship("DocumentPage", back_populates="document", cascade="all, delete-orphan")
    chunks: Mapped[List["DocumentChunk"]] = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")
    extractions: Mapped[List["Extraction"]] = relationship("Extraction", back_populates="document", cascade="all, delete-orphan")


class DocumentPage(Base, TimestampMixin):
    __tablename__ = "document_pages"

    page_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    doc_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("documents.doc_id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    has_text_layer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    document: Mapped["Document"] = relationship("Document", back_populates="pages")

    __table_args__ = (
        Index("ix_document_pages_doc_page", "doc_id", "page_number"),
    )


class DocumentChunk(Base, TimestampMixin):
    __tablename__ = "document_chunks"

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    doc_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("documents.doc_id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = mapped_column(EMBEDDING_COLUMN, nullable=True)

    document: Mapped["Document"] = relationship("Document", back_populates="chunks")


class Extraction(Base, TimestampMixin):
    __tablename__ = "extractions"

    extraction_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    doc_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("documents.doc_id", ondelete="CASCADE"), nullable=False, index=True
    )
    extractor_name: Mapped[str] = mapped_column(String(100), nullable=False)
    raw_output: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")  # passed, failed, approved

    document: Mapped["Document"] = relationship("Document", back_populates="extractions")
    errors: Mapped[List["ExtractionError"]] = relationship("ExtractionError", back_populates="extraction", cascade="all, delete-orphan")


class ExtractionError(Base):
    __tablename__ = "extraction_errors"

    error_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    extraction_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("extractions.extraction_id", ondelete="CASCADE"), nullable=False, index=True
    )
    error_detail: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    extraction: Mapped["Extraction"] = relationship("Extraction", back_populates="errors")
