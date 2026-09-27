import re
from typing import List, Optional
import uuid

from config.errors import LLMProviderError
from domain.ai.llm_provider import HeuristicLLMProvider, LLMProvider
from domain.models.extraction import IncidentExtraction
from services.validation.schemas import IncidentSchema

INCIDENT_PROMPT = """Extract drilling incidents or Non-Productive Time (NPT) events from this narrative paragraph.
Identify: cause, depth in meters (if mentioned), mitigation taken, outcome, description, and confidence.
"""


def _is_tabular_paragraph(para: str) -> bool:
    """Heuristic to detect tabular / log content instead of narrative text."""
    lines = [line.strip() for line in para.split("\n") if line.strip()]
    if not lines:
        return True

    # High frequency of table pipes or tabs
    pipe_count = para.count("|")
    tab_count = para.count("\t")
    if pipe_count >= len(lines) * 2 or tab_count >= len(lines) * 2:
        return True

    # High proportion of numeric tokens
    tokens = para.split()
    if not tokens:
        return True
    numeric_tokens = sum(1 for t in tokens if re.match(r"^[\d\.\,\-\:\/]+$", t))
    if numeric_tokens / len(tokens) > 0.35:
        return True

    return False


def is_narrative_incident_paragraph(para: str, min_words: int = 15) -> bool:
    """Check if paragraph is a narrative paragraph suitable for incident extraction."""
    words = para.strip().split()
    if len(words) < min_words:
        return False
    if _is_tabular_paragraph(para):
        return False
    # Check for incident-related keywords
    incident_keywords = [
        "kick", "loss", "lost", "stuck", "pipe", "instability", "failure",
        "gas", "influx", "washout", "twist", "packoff", "tight", "npt",
        "mud", "circulation", "incident", "pressure", "leak", "blowout"
    ]
    lower = para.lower()
    return any(kw in lower for kw in incident_keywords)


def extract_incidents(
    document_id: uuid.UUID,
    text: str,
    llm: Optional[LLMProvider] = None,
) -> List[IncidentExtraction]:
    """Extract narrative incident paragraphs (cause, depth, mitigation, outcome, auto_title)."""
    # 1. Identify narrative paragraphs (split by double newlines)
    raw_paras = re.split(r"\n\s*\n", text)
    narrative_paras = [p.strip() for p in raw_paras if is_narrative_incident_paragraph(p)]

    if not narrative_paras:
        return []

    provider = llm or HeuristicLLMProvider()
    results: List[IncidentExtraction] = []

    for para in narrative_paras:
        try:
            # 2. LLM call
            extracted = provider.extract_structured(
                prompt=INCIDENT_PROMPT,
                schema=IncidentSchema,
                text=para,
            )
            # 3. Generate short auto-title
            auto_title = provider.summarize_one_line(extracted.description or para)

            results.append(
                IncidentExtraction(
                    cause=extracted.cause,
                    depth=extracted.depth,
                    mitigation=extracted.mitigation,
                    outcome=extracted.outcome,
                    description=extracted.description or para,
                    auto_title=auto_title,
                    confidence=extracted.confidence,
                )
            )
        except LLMProviderError:
            raise
        except Exception:
            continue

    return results
