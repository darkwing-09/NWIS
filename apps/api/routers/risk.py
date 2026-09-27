from typing import List
import uuid
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database.session import get_db
from domain.models.auth import AuthenticatedUser
from domain.models.risk import RiskAssessmentSchema
from services.auth.rbac import require_permission
from services.correlation.engine import run_correlation_for_active_well
from services.risk.service import assess_risk
from services.wells.service import get_well

router = APIRouter(prefix="/risk", tags=["risk"])


@router.get("/{well_id}", response_model=List[RiskAssessmentSchema])
def get_well_risk(
    well_id: uuid.UUID,
    user: AuthenticatedUser = Depends(require_permission("wells:read")),
    db: Session = Depends(get_db),
) -> List[RiskAssessmentSchema]:
    """
    Returns latest risk assessment for active well based on offset well correlation patterns.
    """
    well = get_well(well_id, user=user, db=db)
    # Run cross-well correlation for this well
    correlation_results = run_correlation_for_active_well(well_id=well.well_id, db=db)
    # Assess risk
    assessments = assess_risk(correlation_results, well_id=well.well_id)
    return assessments
