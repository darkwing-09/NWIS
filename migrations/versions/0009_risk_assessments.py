"""create risk_assessments table

Revision ID: 0009_risk_assessments
Revises: 0008_alerts
Create Date: 2026-09-27 15:18:40

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0009_risk_assessments"
down_revision: Union[str, None] = "0008_alerts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "risk_assessments",
        sa.Column("assessment_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("well_id", UUID(as_uuid=True), sa.ForeignKey("wells.well_id", ondelete="CASCADE"), nullable=False),
        sa.Column("risk_level", sa.String(50), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("method", sa.String(100), nullable=False, server_default="rule_based_v1"),
        sa.Column("correlation_result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_risk_assessments_well_id", "risk_assessments", ["well_id"])
    op.create_index("ix_risk_assessments_created_at", "risk_assessments", ["created_at"])
    op.create_index(
        "ix_risk_assessments_well_created",
        "risk_assessments",
        ["well_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("risk_assessments")
