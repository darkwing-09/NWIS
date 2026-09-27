import re
from typing import List, Optional
import uuid
from sqlalchemy.orm import Session

from config.errors import DepthOrderingError, LLMProviderError, OverlapError
from database.repositories.formations import list_intervals_for_well
from domain.ai.llm_provider import HeuristicLLMProvider, LLMProvider
from domain.models.extraction import FormationExtraction
from services.validation.schemas import FormationSchema

FORMATION_PROMPT = """Extract geological formations with top and bottom depths from this text.
Return the formation name, top depth in meters, bottom depth in meters, and confidence score.
"""


def check_interval_overlap(top: float, bottom: float, existing_intervals: list) -> bool:
    """Check if interval [top, bottom] overlaps with any existing interval."""
    for existing in existing_intervals:
        ex_top = existing.top_depth
        ex_bottom = existing.bottom_depth
        # Standard interval overlap condition
        if max(top, ex_top) < min(bottom, ex_bottom):
            return True
    return False


def extract_formations(
    document_id: uuid.UUID,
    text: str,
    well_id: Optional[uuid.UUID] = None,
    db: Optional[Session] = None,
    llm: Optional[LLMProvider] = None,
) -> List[FormationExtraction]:
    """Extract formation intervals with depth validation and overlap detection."""
    provider = llm or HeuristicLLMProvider()

    # Step 1: Call LLM / structured extraction
    try:
        extracted = provider.extract_structured(
            prompt=FORMATION_PROMPT,
            schema=FormationSchema,
            text=text,
        )
        extracted_list = [extracted] if not isinstance(extracted, list) else extracted
    except LLMProviderError:
        raise
    except Exception as e:
        # If schema validation fails on inverted depths at schema level
        if "bottom_depth" in str(e) or "greater than" in str(e):
            return [
                FormationExtraction(
                    formation_name="Unknown",
                    top_depth=0.0,
                    bottom_depth=0.0,
                    confidence=0.0,
                    needs_review=True,
                    error_reason=f"DepthOrderingError: {e}",
                )
            ]
        return [
            FormationExtraction(
                formation_name="Unknown",
                top_depth=0.0,
                bottom_depth=0.0,
                confidence=0.0,
                needs_review=True,
                error_reason=f"Extraction failure: {e}",
            )
        ]

    # Fetch existing intervals if well_id and db provided
    existing_intervals = []
    if well_id and db:
        existing_intervals = list_intervals_for_well(db, well_id)

    results: List[FormationExtraction] = []
    for item in extracted_list:
        fname = item.formation_name
        top = item.top_depth
        bottom = item.bottom_depth
        conf = getattr(item, "confidence", 1.0)

        # 2. Validate: top_depth < bottom_depth
        if top >= bottom:
            results.append(
                FormationExtraction(
                    formation_name=fname,
                    top_depth=top,
                    bottom_depth=bottom,
                    confidence=conf,
                    needs_review=True,
                    error_reason=f"DepthOrderingError: top_depth ({top}m) >= bottom_depth ({bottom}m)",
                )
            )
            continue

        # 3. Validate: no overlapping intervals within same well
        if check_interval_overlap(top, bottom, existing_intervals):
            results.append(
                FormationExtraction(
                    formation_name=fname,
                    top_depth=top,
                    bottom_depth=bottom,
                    confidence=conf,
                    needs_review=True,
                    error_reason=f"OverlapError: interval [{top}m, {bottom}m] overlaps with existing formation",
                )
            )
            continue

        # Valid interval
        results.append(
            FormationExtraction(
                formation_name=fname,
                top_depth=top,
                bottom_depth=bottom,
                confidence=conf,
                needs_review=False,
            )
        )

    return results
