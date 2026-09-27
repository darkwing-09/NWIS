"""create well formation intervals table

Revision ID: 0002_formation_intervals
Revises: 0001_wells
Create Date: 2026-09-27 15:17:30

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0002_formation_intervals"
down_revision: Union[str, None] = "0001_wells"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "well_formation_intervals",
        sa.Column("interval_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("well_id", UUID(as_uuid=True), sa.ForeignKey("wells.well_id", ondelete="CASCADE"), nullable=False),
        sa.Column("formation_name", sa.String(255), nullable=False),
        sa.Column("top_depth", sa.Float(), nullable=False),
        sa.Column("bottom_depth", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("top_depth < bottom_depth", name="check_top_depth_less_than_bottom_depth"),
    )
    op.create_index("ix_well_formation_intervals_well_id", "well_formation_intervals", ["well_id"])
    op.create_index("ix_well_formation_intervals_formation_name", "well_formation_intervals", ["formation_name"])
    op.create_index(
        "ix_well_formation_intervals_well_formation",
        "well_formation_intervals",
        ["well_id", "formation_name"],
    )


def downgrade() -> None:
    op.drop_table("well_formation_intervals")
