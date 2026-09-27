"""create documents table

Revision ID: 0004_documents
Revises: 0003_auth
Create Date: 2026-09-27 15:17:50

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0004_documents"
down_revision: Union[str, None] = "0003_auth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("doc_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("well_id", UUID(as_uuid=True), sa.ForeignKey("wells.well_id", ondelete="SET NULL"), nullable=True),
        sa.Column("doc_type", sa.String(50), nullable=False),
        sa.Column("file_path", sa.String(1024), nullable=False),
        sa.Column("ocr_status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("extraction_status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("raw_extracted_text", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("amends_document_id", UUID(as_uuid=True), sa.ForeignKey("documents.doc_id"), nullable=True),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("data_classification", sa.String(50), nullable=False, server_default="internal"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_documents_well_id", "documents", ["well_id"])
    op.create_index("ix_documents_ocr_status", "documents", ["ocr_status"])
    op.create_index("ix_documents_extraction_status", "documents", ["extraction_status"])


def downgrade() -> None:
    op.drop_table("documents")
