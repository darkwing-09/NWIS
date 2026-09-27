"""create integration_events table

Revision ID: 0012_integration_events
Revises: 0011_processing_jobs
Create Date: 2026-09-27 15:19:10

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0012_integration_events"
down_revision: Union[str, None] = "0011_processing_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "integration_events",
        sa.Column("event_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("well_id", UUID(as_uuid=True), sa.ForeignKey("wells.well_id", ondelete="CASCADE"), nullable=False),
        sa.Column("depth", sa.Float(), nullable=False),
        sa.Column("formation", sa.String(255), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_integration_events_well_id", "integration_events", ["well_id"])
    op.create_index(
        "ix_integration_events_well_time",
        "integration_events",
        ["well_id", "timestamp"],
    )


def downgrade() -> None:
    op.drop_table("integration_events")
