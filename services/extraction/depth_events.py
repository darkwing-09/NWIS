import re
from typing import List, Optional
import uuid
from pydantic import ValidationError

from config.errors import LLMProviderError
from domain.ai.llm_provider import HeuristicLLMProvider, LLMProvider
from domain.models.enums import EventType, normalize_event_type
from domain.models.extraction import DepthEventExtraction
from services.validation.schemas import DepthEventSchema

DEPTH_PATTERN = re.compile(r"\b\d{3,5}(?:\.\d+)?\s*m\b", re.IGNORECASE)

DEPTH_EVENT_PROMPT = """Extract drilling depth events from this text context.
Identify the depth in meters (canonical 'm'), the event_type (kick, loss_circulation, stuck_pipe, wellbore_instability, equipment_failure, gas_show, packoff, tight_hole, casing_leak, water_influx, other), and description.
"""


def _split_into_sentences(text: str) -> List[str]:
    """Split text into sentences/lines preserving paragraph breaks."""
    raw_sentences = re.split(r"(?<=[.!?\n])\s+", text)
    return [s.strip() for s in raw_sentences if s.strip()]


def extract_depth_events(
    document_id: uuid.UUID,
    text: str,
    llm: Optional[LLMProvider] = None,
) -> List[DepthEventExtraction]:
    """Extract (event_type, depth, description) triples using regex pre-filter + LLM extraction."""
    sentences = _split_into_sentences(text)
    if not sentences:
        return []

    # 1. Regex pre-filter: find sentences matching DEPTH_PATTERN
    matched_indices = []
    for idx, sentence in enumerate(sentences):
        if DEPTH_PATTERN.search(sentence):
            matched_indices.append(idx)

    if not matched_indices:
        return []

    provider = llm or HeuristicLLMProvider()
    results: List[DepthEventExtraction] = []
    seen_windows = set()

    for idx in matched_indices:
        # 2. +/- 1 sentence context window
        start = max(0, idx - 1)
        end = min(len(sentences), idx + 2)
        window = " ".join(sentences[start:end])
        if window in seen_windows:
            continue
        seen_windows.add(window)

        try:
            extracted = provider.extract_structured(
                prompt=DEPTH_EVENT_PROMPT,
                schema=DepthEventSchema,
                text=window,
            )
            # Check if canonical EventType
            ev_type_val = extracted.event_type.value if isinstance(extracted.event_type, EventType) else str(extracted.event_type)
            norm_type = normalize_event_type(ev_type_val)

            if norm_type is None:
                # 3. Reject any result where event_type not in EventType enum -> needs_review
                results.append(
                    DepthEventExtraction(
                        event_type=ev_type_val,
                        depth=extracted.depth,
                        description=extracted.description,
                        confidence=extracted.confidence,
                        needs_review=True,
                        error_reason=f"Invalid event_type '{ev_type_val}' not in EventType enum",
                    )
                )
            else:
                results.append(
                    DepthEventExtraction(
                        event_type=norm_type.value,
                        depth=extracted.depth,
                        description=extracted.description,
                        confidence=extracted.confidence,
                        needs_review=False,
                    )
                )
        except ValidationError as ve:
            # Schema validation failed (e.g. invalid enum or bounds) -> route to review, not dropped
            depth_match = DEPTH_PATTERN.search(window)
            fallback_depth = float(re.search(r"\d+", depth_match.group(0)).group(0)) if depth_match else 0.0
            results.append(
                DepthEventExtraction(
                    event_type="unknown",
                    depth=fallback_depth,
                    description=window[:300],
                    confidence=0.5,
                    needs_review=True,
                    error_reason=f"Schema validation error: {ve}",
                )
            )
        except LLMProviderError:
            raise
        except Exception as e:
            # Route to review
            results.append(
                DepthEventExtraction(
                    event_type="unknown",
                    depth=0.0,
                    description=window[:300],
                    confidence=0.0,
                    needs_review=True,
                    error_reason=f"Extraction failure: {e}",
                )
            )

    return results
