"""create events, incident_events, drilling_parameters tables

Revision ID: 0007_events
Revises: 0006_extractions
Create Date: 2026-09-27 15:18:20

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0007_events"
down_revision: Union[str, None] = "0006_extractions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("event_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("well_id", UUID(as_uuid=True), sa.ForeignKey("wells.well_id", ondelete="CASCADE"), nullable=False),
        sa.Column("depth", sa.Float(), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(50), nullable=False, server_default="medium"),
        sa.Column("description_raw", sa.Text(), nullable=False),
        sa.Column("mitigation_taken", sa.Text(), nullable=True),
        sa.Column("source_document_id", UUID(as_uuid=True), sa.ForeignKey("documents.doc_id", ondelete="SET NULL"), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("extractor_version", sa.String(100), nullable=False, server_default="1.0.0"),
        sa.Column("model_version", sa.String(100), nullable=True),
        sa.Column("superseded_by_event_id", UUID(as_uuid=True), sa.ForeignKey("events.event_id", ondelete="SET NULL"), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duplicate_group_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_events_well_id", "events", ["well_id"])
    op.create_index("ix_events_depth", "events", ["depth"])
    op.create_index("ix_events_event_type", "events", ["event_type"])
    op.create_index("ix_events_source_document_id", "events", ["source_document_id"])
    op.create_index("ix_events_duplicate_group_id", "events", ["duplicate_group_id"])
    op.create_index("ix_events_well_depth", "events", ["well_id", "depth"])

    op.create_table(
        "incident_events",
        sa.Column("event_id", UUID(as_uuid=True), sa.ForeignKey("events.event_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("cause", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("auto_title", sa.String(255), nullable=True),
    )

    op.create_table(
        "drilling_parameters",
        sa.Column("param_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("well_id", UUID(as_uuid=True), sa.ForeignKey("wells.well_id", ondelete="CASCADE"), nullable=False),
        sa.Column("depth", sa.Float(), nullable=False),
        sa.Column("mud_weight", sa.Float(), nullable=True),
        sa.Column("casing_size", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_drilling_parameters_well_id", "drilling_parameters", ["well_id"])
    op.create_index("ix_drilling_parameters_depth", "drilling_parameters", ["depth"])


def downgrade() -> None:
    op.drop_table("drilling_parameters")
    op.drop_table("incident_events")
    op.drop_table("events")
