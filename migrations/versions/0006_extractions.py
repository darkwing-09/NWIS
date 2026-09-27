"""create extractions and extraction_errors tables

Revision ID: 0006_extractions
Revises: 0005_document_pages_chunks
Create Date: 2026-09-27 15:18:10

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0006_extractions"
down_revision: Union[str, None] = "0005_document_pages_chunks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "extractions",
        sa.Column("extraction_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("doc_id", UUID(as_uuid=True), sa.ForeignKey("documents.doc_id", ondelete="CASCADE"), nullable=False),
        sa.Column("extractor_name", sa.String(100), nullable=False),
        sa.Column("raw_output", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_extractions_doc_id", "extractions", ["doc_id"])

    op.create_table(
        "extraction_errors",
        sa.Column("error_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("extraction_id", UUID(as_uuid=True), sa.ForeignKey("extractions.extraction_id", ondelete="CASCADE"), nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_extraction_errors_extraction_id", "extraction_errors", ["extraction_id"])


def downgrade() -> None:
    op.drop_table("extraction_errors")
    op.drop_table("extractions")
