"""create alerts and alert_evidence tables

Revision ID: 0008_alerts
Revises: 0007_events
Create Date: 2026-09-27 15:18:30

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0008_alerts"
down_revision: Union[str, None] = "0007_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "alerts",
        sa.Column("alert_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("well_id", UUID(as_uuid=True), sa.ForeignKey("wells.well_id", ondelete="CASCADE"), nullable=False),
        sa.Column("risk_level", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="CREATED"),
        sa.Column("contributing_wells", sa.JSON(), nullable=False),
        sa.Column("recommended_mitigation", sa.Text(), nullable=True),
        sa.Column("acknowledged_by", UUID(as_uuid=True), sa.ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dedup_key", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_alerts_well_id", "alerts", ["well_id"])
    op.create_index("ix_alerts_status", "alerts", ["status"])
    op.create_index("ix_alerts_dedup_key", "alerts", ["dedup_key"])
    op.create_index("ix_alerts_well_status", "alerts", ["well_id", "status"])

    op.create_table(
        "alert_evidence",
        sa.Column("alert_id", UUID(as_uuid=True), sa.ForeignKey("alerts.alert_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("event_id", UUID(as_uuid=True), sa.ForeignKey("events.event_id", ondelete="CASCADE"), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("alert_evidence")
    op.drop_table("alerts")
