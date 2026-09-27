"""create wells table

Revision ID: 0001_wells
Revises: 
Create Date: 2026-09-27 15:17:00

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0001_wells"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostGIS extension if PostgreSQL
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "wells",
        sa.Column("well_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("spud_date", sa.Date(), nullable=True),
        sa.Column("operator_name", sa.String(255), nullable=True),
        sa.Column("field_name", sa.String(255), nullable=True),
        sa.Column("basin_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_wells_name", "wells", ["name"])
    op.create_index("ix_wells_status", "wells", ["status"])
    op.create_index("ix_wells_field_name", "wells", ["field_name"])

    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE wells ADD COLUMN geom geography(Point, 4326)")
        op.execute("CREATE INDEX idx_wells_geom ON wells USING GIST(geom)")
        op.execute("CREATE INDEX idx_wells_name_trgm ON wells USING GIN(name gin_trgm_ops)")


def downgrade() -> None:
    op.drop_table("wells")
