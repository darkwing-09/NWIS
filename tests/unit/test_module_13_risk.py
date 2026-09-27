import uuid
import pytest
from datetime import datetime, timezone

from config.errors import ModelNotApprovedError, ValidationError
from config.settings import Settings
from domain.models.correlation import CorrelationResult
from domain.models.risk import EvaluationReport, RiskFeatures, RiskPrediction
from services.risk.registry import ModelRegistry
from services.risk.rule_based import apply_rule_based_threshold
from services.risk import statistical
from services.risk import service


def _make_correlation_result(event_type: str = "stuck_pipe", count: int = 1) -> CorrelationResult:
    return CorrelationResult(
        event_type=event_type,
        distinct_well_count=count,
        contributing_wells=[f"WELL_{i}" for i in range(count)],
        contributing_well_ids=[uuid.uuid4() for _ in range(count)],
        event_ids=[uuid.uuid4() for _ in range(count)],
    )


# --- 13.1 Rule Based Tests ---

def test_three_wells_is_high():
    well_id = uuid.uuid4()
    res = _make_correlation_result("loss_circulation", count=3)
    assessments = apply_rule_based_threshold([res], well_id)
    assert len(assessments) == 1
    assert assessments[0].risk_level == "high"
    assert assessments[0].method == "rule_based_v1"
    assert assessments[0].confidence is None


def test_two_wells_is_medium():
    well_id = uuid.uuid4()
    res = _make_correlation_result("loss_circulation", count=2)
    assessments = apply_rule_based_threshold([res], well_id)
    assert len(assessments) == 1
    assert assessments[0].risk_level == "medium"


def test_one_well_is_low():
    well_id = uuid.uuid4()
    res = _make_correlation_result("loss_circulation", count=1)
    assessments = apply_rule_based_threshold([res], well_id)
    assert len(assessments) == 1
    assert assessments[0].risk_level == "low"


def test_threshold_values_read_from_config_not_hardcoded():
    well_id = uuid.uuid4()
    res = _make_correlation_result("kick", count=2)
    # Custom settings: medium threshold at 3, high at 5
    custom_cfg = Settings(risk_threshold_medium=3, risk_threshold_high=5)
    assessments = apply_rule_based_threshold([res], well_id, settings=custom_cfg)
    assert len(assessments) == 1
    # count=2 is below custom medium threshold 3 -> should be low
    assert assessments[0].risk_level == "low"


# --- 13.2 Statistical Model Tests ---

def test_raises_if_model_not_approved():
    registry = ModelRegistry()
    unapproved_report = EvaluationReport(
        model_name="risk_stat_v1",
        model_version="1.0.0",
        approved=False,
    )
    registry.register_model(
        name="risk_stat_v1",
        version="1.0.0",
        model_obj=None,
        evaluation_report=unapproved_report,
        mode="active",
    )

    features = RiskFeatures(
        well_id=uuid.uuid4(),
        depth=2450.0,
        formation="Barail",
        event_type="stuck_pipe",
        distinct_well_count=2,
    )

    with pytest.raises(ModelNotApprovedError):
        statistical.predict(features, registry=registry)


def test_returns_calibrated_probability():
    registry = ModelRegistry()
    approved_report = EvaluationReport(
        model_name="risk_stat_v1",
        model_version="1.0.0",
        approved=True,
        approved_by="chief_drilling_engineer",
        approved_at=datetime.now(timezone.utc),
    )
    registry.register_model(
        name="risk_stat_v1",
        version="1.0.0",
        model_obj=None,
        evaluation_report=approved_report,
        mode="active",
    )

    features = RiskFeatures(
        well_id=uuid.uuid4(),
        depth=2450.0,
        formation="Barail",
        event_type="stuck_pipe",
        distinct_well_count=3,
    )

    pred = statistical.predict(features, registry=registry)
    assert isinstance(pred, RiskPrediction)
    assert pred.event_type == "stuck_pipe"
    assert 0.0 <= pred.calibrated_probability <= 1.0
    assert pred.model_version == "1.0.0"


def test_feature_schema_mismatch_raises():
    registry = ModelRegistry()
    with pytest.raises(ValidationError):
        # Passing invalid object type instead of RiskFeatures
        statistical.predict({"well_id": "bad"}, registry=registry)  # type: ignore


# --- 13.3 Service Orchestration Tests ---

def test_returns_stage1_only_when_no_model():
    well_id = uuid.uuid4()
    registry = ModelRegistry()  # Empty registry
    res = [_make_correlation_result("stuck_pipe", count=3)]
    assessments = service.assess_risk(res, well_id, registry=registry)
    assert len(assessments) == 1
    assert assessments[0].risk_level == "high"
    assert assessments[0].method == "rule_based_v1"


def test_shadow_mode_logs_but_returns_stage1(caplog):
    well_id = uuid.uuid4()
    registry = ModelRegistry()
    approved_report = EvaluationReport(
        model_name="risk_stat_shadow",
        model_version="1.0.0",
        approved=True,
    )
    registry.register_model(
        name="risk_stat_shadow",
        version="1.0.0",
        model_obj=None,
        evaluation_report=approved_report,
        mode="shadow",
    )

    res = [_make_correlation_result("loss_circulation", count=1)]
    assessments = service.assess_risk(res, well_id, registry=registry)

    # Returns Stage 1 output (low)
    assert len(assessments) == 1
    assert assessments[0].risk_level == "low"
    assert assessments[0].method == "rule_based_v1"


def test_active_mode_can_raise_but_not_lower_risk_level():
    well_id = uuid.uuid4()
    registry = ModelRegistry()

    # Model that predicts 'low' risk
    class LowRiskModel:
        def predict(self, features):
            return RiskPrediction(
                event_type=features.event_type,
                predicted_risk_level="low",
                calibrated_probability=0.15,
                model_version="low_v1",
            )

    approved_report = EvaluationReport(
        model_name="low_model",
        model_version="low_v1",
        approved=True,
    )
    registry.register_model(
        name="low_model",
        version="low_v1",
        model_obj=LowRiskModel(),
        evaluation_report=approved_report,
        mode="active",
    )

    # Stage 1 produces 'high' because distinct_well_count=3
    res = [_make_correlation_result("kick", count=3)]
    assessments = service.assess_risk(res, well_id, registry=registry)

    # Acceptance criteria: system NEVER returns a risk level below what Stage 1 alone would produce
    assert len(assessments) == 1
    assert assessments[0].risk_level == "high"
    assert "safety_floor" in assessments[0].method

    # Now test model that raises risk from low to high
    class HighRiskModel:
        def predict(self, features):
            return RiskPrediction(
                event_type=features.event_type,
                predicted_risk_level="high",
                calibrated_probability=0.92,
                model_version="high_v1",
            )

    registry2 = ModelRegistry()
    registry2.register_model(
        name="high_model",
        version="high_v1",
        model_obj=HighRiskModel(),
        evaluation_report=EvaluationReport(
            model_name="high_model",
            model_version="high_v1",
            approved=True,
        ),
        mode="active",
    )
    # Stage 1 produces 'low' because count=1
    res_low = [_make_correlation_result("kick", count=1)]
    assessments_raised = service.assess_risk(res_low, well_id, registry=registry2)
    assert len(assessments_raised) == 1
    assert assessments_raised[0].risk_level == "high"
    assert "elevated" in assessments_raised[0].method


def test_model_error_falls_back_gracefully():
    well_id = uuid.uuid4()
    registry = ModelRegistry()

    class FaultyModel:
        def predict(self, features):
            raise RuntimeError("CUDA out of memory / model crash")

    registry.register_model(
        name="faulty_model",
        version="faulty_v1",
        model_obj=FaultyModel(),
        evaluation_report=EvaluationReport(
            model_name="faulty_model",
            model_version="faulty_v1",
            approved=True,
        ),
        mode="active",
    )

    res = [_make_correlation_result("stuck_pipe", count=2)]
    # Should fall back to Stage 1 without throwing
    assessments = service.assess_risk(res, well_id, registry=registry)
    assert len(assessments) == 1
    assert assessments[0].risk_level == "medium"
