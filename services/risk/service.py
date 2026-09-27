import logging
from typing import List, Optional
import uuid
import structlog

from config.errors import ModelNotApprovedError
from config.settings import Settings, get_settings
from domain.models.correlation import CorrelationResult
from domain.models.risk import RiskAssessmentSchema, RiskFeatures
from services.risk.registry import ModelRegistry, get_model_registry
from services.risk.rule_based import apply_rule_based_threshold
from services.risk import statistical

logger = structlog.get_logger("nwis.risk")

RISK_RANKS = {"low": 1, "medium": 2, "high": 3}


def log_stage_comparison(stage1: List[RiskAssessmentSchema], stage2_preds: list) -> None:
    """Logs comparison between Stage 1 rule-based and Stage 2 statistical predictions for drift monitoring."""
    logger.info(
        "stage_comparison_logged",
        stage1_count=len(stage1),
        stage2_count=len(stage2_preds),
        details=[{"s1": a.risk_level} for a in stage1],
    )


def merge_with_stage1_as_floor(
    stage1_assessments: List[RiskAssessmentSchema],
    stage2_predictions: list,
) -> List[RiskAssessmentSchema]:
    """
    Stage 2 can raise risk level, but NEVER lower it below stage1's rule-based score.
    Enforces the defensive safety floor required by the production architecture.
    """
    merged: List[RiskAssessmentSchema] = []

    pred_map = {p.event_type: p for p in stage2_predictions}

    for s1 in stage1_assessments:
        event_type = s1.contributing_evidence.event_type
        pred = pred_map.get(event_type)

        if not pred:
            merged.append(s1)
            continue

        s1_rank = RISK_RANKS.get(s1.risk_level.lower(), 1)
        s2_rank = RISK_RANKS.get(pred.predicted_risk_level.lower(), 1)

        if s2_rank > s1_rank:
            # Stage 2 raised the risk level
            final_level = pred.predicted_risk_level.lower()
            method = f"active_model_{pred.model_version}_elevated"
            confidence = pred.calibrated_probability
        else:
            # Stage 1 safety floor preserved
            final_level = s1.risk_level.lower()
            method = f"rule_based_safety_floor (s2={pred.predicted_risk_level.lower()})"
            confidence = pred.calibrated_probability

        merged.append(
            RiskAssessmentSchema(
                assessment_id=s1.assessment_id,
                well_id=s1.well_id,
                risk_level=final_level,
                confidence=confidence,
                method=method,
                contributing_evidence=s1.contributing_evidence,
            )
        )

    return merged


def assess_risk(
    correlation_results: List[CorrelationResult],
    well_id: uuid.UUID,
    registry: Optional[ModelRegistry] = None,
    settings: Optional[Settings] = None,
) -> List[RiskAssessmentSchema]:
    """
    Single entrypoint for risk assessment.
    1. stage1 = apply_rule_based_threshold(correlation_results, well_id) (always runs)
    2. if shadow model active: predict in shadow mode and log comparison (never returned to caller)
    3. if active model active: predict and merge with Stage 1 as defensive floor
    4. on model error: fall back to stage 1 and log warning
    """
    reg = registry or get_model_registry()
    cfg = settings or get_settings()

    # Step 1: Stage 1 rule-based threshold scorer (always runs)
    stage1 = apply_rule_based_threshold(correlation_results, well_id, settings=cfg)

    # Step 2: Shadow mode evaluation
    if reg.has_active_model(mode="shadow"):
        shadow_preds = []
        try:
            shadow_model = reg.get_active_model(mode="shadow")
            for res in correlation_results:
                features = RiskFeatures(
                    well_id=well_id,
                    depth=0.0,
                    formation="",
                    event_type=res.event_type,
                    distinct_well_count=res.distinct_well_count,
                    offset_distances_km=[],
                )
                pred = statistical.predict(features, registry=reg, mode="shadow")
                shadow_preds.append(pred)
            log_stage_comparison(stage1, shadow_preds)
        except Exception as e:
            logger.warning("shadow_mode_evaluation_failed", error=str(e))

    # Step 3: Active mode model
    if reg.has_active_model(mode="active"):
        try:
            active_preds = []
            for res in correlation_results:
                features = RiskFeatures(
                    well_id=well_id,
                    depth=0.0,
                    formation="",
                    event_type=res.event_type,
                    distinct_well_count=res.distinct_well_count,
                    offset_distances_km=[],
                )
                pred = statistical.predict(features, registry=reg)
                active_preds.append(pred)

            return merge_with_stage1_as_floor(stage1, active_preds)

        except ModelNotApprovedError as e:
            logger.warning("active_model_not_approved_falling_back_to_stage1", error=str(e))
            return stage1
        except Exception as e:
            logger.warning("active_model_error_falling_back_to_stage1", error=str(e))
            return stage1

    return stage1
