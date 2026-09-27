from datetime import date, datetime
from difflib import SequenceMatcher
import re
from typing import Optional
import uuid
from sqlalchemy.orm import Session

from config.errors import LLMProviderError
from database.repositories.wells import fuzzy_match_well_name
from domain.ai.llm_provider import LLMProvider
from domain.models.extraction import WellMetadataExtraction
from services.validation.schemas import WellMetadataSchema

WELL_METADATA_PROMPT = """Extract well metadata from this document header.
Return JSON matching WellMetadataSchema: well_name, spud_date (YYYY-MM-DD), operator, and confidence (0.0 to 1.0).
"""


def _parse_date(val: Optional[str]) -> Optional[date]:
    if not val:
        return None
    val = val.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(val, fmt).date()
        except ValueError:
            continue
    return None


def extract_well_metadata(
    document_id: uuid.UUID,
    text: str,
    db: Optional[Session] = None,
    llm: Optional[LLMProvider] = None,
) -> WellMetadataExtraction:
    """Extract well metadata (well_name, spud_date, operator) from header region."""
    header_text = text[:2000]

    well_name: Optional[str] = None
    spud_date: Optional[date] = None
    operator: Optional[str] = None

    # 1. Regex pass over first ~2000 chars for fixed-format header fields
    well_match = re.search(
        r"(?:Well\s*(?:Name|No|#)?|WELL\s*(?:NAME|NO|#)?)\s*[:=\-]?\s*([A-Za-z0-9_\-]+)",
        header_text,
    )
    if well_match:
        well_name = well_match.group(1).strip()

    spud_match = re.search(
        r"(?:Spud\s*Date|SPUD\s*DATE|Date\s*of\s*Spud)\s*[:=\-]?\s*(\d{4}[-/]\d{2}[-/]\d{2}|\d{2}[-/]\d{2}[-/]\d{4})",
        header_text,
    )
    if spud_match:
        spud_date = _parse_date(spud_match.group(1))

    op_match = re.search(
        r"(?:Operator|OPERATOR)\s*[:=\-]?\s*([A-Za-z0-9\s,\.]+?)(?=\n|Well|Date|Field|$)",
        header_text,
    )
    if op_match:
        operator = op_match.group(1).strip()

    # 2. For fields regex misses: call LLM provider
    if (not well_name or not spud_date or not operator) and llm is not None:
        try:
            extracted = llm.extract_structured(
                prompt=WELL_METADATA_PROMPT,
                schema=WellMetadataSchema,
                text=text[:4000],
            )
            if not well_name and extracted.well_name:
                well_name = extracted.well_name
            if not spud_date and extracted.spud_date:
                spud_date = extracted.spud_date
            if not operator and extracted.operator:
                operator = extracted.operator
        except LLMProviderError:
            raise
        except Exception as e:
            raise LLMProviderError(f"LLM extraction error: {e}") from e

    # 3. Fuzzy-match extracted well name against wells table
    matched_well_id: Optional[uuid.UUID] = None
    match_confidence: float = 0.0

    if well_name and db is not None:
        matched_well = fuzzy_match_well_name(db, well_name, threshold=0.6)
        if matched_well:
            matched_well_id = matched_well.well_id
            match_confidence = SequenceMatcher(
                None, well_name.lower(), matched_well.name.lower()
            ).ratio()

    # 4. Return WellMetadataExtraction
    return WellMetadataExtraction(
        well_name=well_name,
        spud_date=spud_date,
        operator=operator,
        matched_well_id=matched_well_id,
        match_confidence=match_confidence,
    )
