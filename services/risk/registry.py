from typing import Any, Dict, Optional
from domain.models.risk import EvaluationReport


class RegisteredModel:
    def __init__(
        self,
        name: str,
        version: str,
        model_obj: Any,
        evaluation_report: EvaluationReport,
        mode: str = "shadow",  # shadow or active
    ) -> None:
        self.name = name
        self.version = version
        self.model_obj = model_obj
        self.evaluation_report = evaluation_report
        self.mode = mode


class ModelRegistry:
    """MLOps model registry governing promotion and evaluation reports."""

    def __init__(self) -> None:
        self._models: Dict[str, RegisteredModel] = {}

    def register_model(
        self,
        name: str,
        version: str,
        model_obj: Any,
        evaluation_report: EvaluationReport,
        mode: str = "shadow",
    ) -> None:
        key = f"{name}:{version}"
        self._models[key] = RegisteredModel(
            name=name,
            version=version,
            model_obj=model_obj,
            evaluation_report=evaluation_report,
            mode=mode,
        )

    def has_active_model(self, mode: str = "active") -> bool:
        return any(m.mode == mode for m in self._models.values())

    def get_active_model(self, mode: str = "active") -> Optional[RegisteredModel]:
        for m in self._models.values():
            if m.mode == mode:
                return m
        return None

    def clear(self) -> None:
        self._models.clear()


_registry = ModelRegistry()


def get_model_registry() -> ModelRegistry:
    return _registry
