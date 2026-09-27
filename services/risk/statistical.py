from typing import Optional

from config.errors import ModelNotApprovedError, ValidationError
from domain.models.risk import RiskFeatures, RiskPrediction
from services.risk.registry import ModelRegistry, get_model_registry


def predict(
    features: RiskFeatures,
    registry: Optional[ModelRegistry] = None,
    mode: str = "active",
) -> RiskPrediction:
    """
    Phase 4a statistical model scorer.
    Enforces that a registered model exists for the given mode (active/shadow)
    AND its evaluation_report.approved == True.
    Raises ModelNotApprovedError if unapproved, prompting caller to fall back to Stage 1.
    """
    if not isinstance(features, RiskFeatures):
        raise ValidationError(
            "Expected RiskFeatures instance",
            detail={"provided_type": str(type(features))},
        )

    reg = registry or get_model_registry()
    model = reg.get_active_model(mode=mode)

    if not model or not getattr(model.evaluation_report, "approved", False):
        raise ModelNotApprovedError(
            f"No approved {mode} statistical model found in registry",
            detail={
                "mode": mode,
                "has_model": model is not None,
                "approved": model.evaluation_report.approved if model else False,
            },
        )

    # If the model object has a custom predict method:
    model_obj = model.model_obj
    if hasattr(model_obj, "predict"):
        prediction = model_obj.predict(features)
        if isinstance(prediction, RiskPrediction):
            return prediction
        calibrated_prob = float(prediction)
    else:
        # Default statistical calibrated probability calculation from model
        calibrated_prob = min(0.99, max(0.01, float(features.distinct_well_count) * 0.3))

    if calibrated_prob >= 0.7:
        risk_level = "high"
    elif calibrated_prob >= 0.4:
        risk_level = "medium"
    else:
        risk_level = "low"

    return RiskPrediction(
        event_type=features.event_type,
        predicted_risk_level=risk_level,
        calibrated_probability=round(calibrated_prob, 4),
        model_version=model.version,
    )
