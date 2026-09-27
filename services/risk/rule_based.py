from typing import List, Optional
import uuid

from config.settings import Settings, get_settings
from domain.models.correlation import CorrelationResult
from domain.models.risk import RiskAssessmentSchema


def apply_rule_based_threshold(
    results: List[CorrelationResult],
    well_id: uuid.UUID,
    settings: Optional[Settings] = None,
) -> List[RiskAssessmentSchema]:
    """
    Stage 1 risk scorer: evaluates pattern frequency against configurable thresholds.
    Config-driven: NWIS_RISK_THRESHOLD_MEDIUM (default 2), NWIS_RISK_THRESHOLD_HIGH (default 3).
    """
    cfg = settings or get_settings()
    threshold_high = cfg.risk_threshold_high
    threshold_medium = cfg.risk_threshold_medium

    assessments: List[RiskAssessmentSchema] = []

    for res in results:
        count = res.distinct_well_count
        if count >= threshold_high:
            risk_level = "high"
        elif count >= threshold_medium:
            risk_level = "medium"
        elif count >= 1:
            risk_level = "low"
        else:
            continue

        assessments.append(
            RiskAssessmentSchema(
                well_id=well_id,
                risk_level=risk_level,
                confidence=None,
                method="rule_based_v1",
                contributing_evidence=res,
            )
        )

    return assessments
