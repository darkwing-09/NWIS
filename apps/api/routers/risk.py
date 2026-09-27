from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from database.session import get_db
from domain.models.auth import AuthenticatedUser
from domain.models.risk import RiskAssessmentSchema
from services.auth.rbac import require_permission
from services.correlation.engine import run_correlation
from services.risk.service import assess_risk
from services.wells.service import get_well

router = APIRouter(prefix="/risk", tags=["risk"])


@router.get("/{well_id}", response_model=List[RiskAssessmentSchema])
def get_well_risk(
    well_id: uuid.UUID,
    depth: float = Query(2450.0, description="Active drilling depth in meters"),
    formation: str = Query("Barail", description="Target formation"),
    radius_km: Optional[float] = Query(None, description="Search radius in km"),
    user: AuthenticatedUser = Depends(require_permission("wells:read")),
    db: Session = Depends(get_db),
) -> List[RiskAssessmentSchema]:
    """
    Returns latest risk assessment for active well based on offset well correlation patterns.
    """
    well = get_well(well_id, user=user, db=db)
    # Run deterministic cross-well correlation
    correlation_results = run_correlation(
        well_id=well.well_id,
        depth=depth,
        formation=formation,
        radius_km=radius_km,
        db=db,
    )
    # Assess risk
    assessments = assess_risk(correlation_results, well_id=well.well_id)
    return assessments
