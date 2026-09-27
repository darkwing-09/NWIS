from typing import Any, Dict, Optional
import uuid
from pydantic import BaseModel, ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from config.errors import NWISError, ValidationError
from database.models.documents import ExtractionError
from services.validation.schemas import SCHEMA_REGISTRY


class ValidationResult(BaseModel):
    passed: bool
    data: Optional[Any] = None
    error: Optional[str] = None


def validate(
    extraction_id: uuid.UUID,
    extractor_name: str,
    raw_output: Dict[str, Any],
    db: Optional[Session] = None,
) -> ValidationResult:
    """Single shared schema gate for all extractor outputs.
    Guarantees no raw LLM output reaches trusted tables without validation."""
    if extractor_name not in SCHEMA_REGISTRY:
        raise NWISError(
            f"Unknown extractor name '{extractor_name}' not registered in SCHEMA_REGISTRY",
            code="config_error",
        )

    schema_cls = SCHEMA_REGISTRY[extractor_name]

    try:
        validated = schema_cls(**raw_output)
        return ValidationResult(passed=True, data=validated.model_dump(), error=None)
    except (PydanticValidationError, ValueError, TypeError) as e:
        error_msg = str(e)
        if db is not None:
            err_row = ExtractionError(
                error_id=uuid.uuid4(),
                extraction_id=extraction_id,
                error_detail=error_msg,
            )
            db.add(err_row)
            try:
                db.commit()
            except Exception:
                db.rollback()

        return ValidationResult(passed=False, data=None, error=error_msg)
