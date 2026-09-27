import re
from typing import Any, Callable, Dict, Optional, Protocol, Type, TypeVar, runtime_checkable
from pydantic import BaseModel

from config.errors import LLMProviderError
from domain.models.enums import EventType, normalize_event_type

T = TypeVar("T", bound=BaseModel)


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol for LLM interactions. Swappable behind one interface."""

    def extract_structured(self, prompt: str, schema: Type[T], text: str) -> T:
        """Extract structured data adhering to a Pydantic schema."""
        ...

    def summarize_one_line(self, text: str) -> str:
        """Produce a single-line summary of the text."""
        ...

    def generate_text(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Generate unstructured text (e.g. for RAG synthesis)."""
        ...


class MockLLMProvider:
    """Mock LLM provider for deterministic testing."""

    def __init__(
        self,
        canned_extractions: Optional[Dict[str, Any]] = None,
        canned_summaries: Optional[Dict[str, str]] = None,
        canned_text: Optional[str] = None,
        should_raise: bool = False,
    ):
        self.canned_extractions = canned_extractions or {}
        self.canned_summaries = canned_summaries or {}
        self.canned_text = canned_text
        self.should_raise = should_raise
        self.call_history = []

    def extract_structured(self, prompt: str, schema: Type[T], text: str) -> T:
        self.call_history.append({"method": "extract_structured", "prompt": prompt, "schema": schema.__name__, "text": text})
        if self.should_raise:
            raise LLMProviderError("Mock LLM error")

        schema_name = schema.__name__
        if schema_name in self.canned_extractions:
            val = self.canned_extractions[schema_name]
            if isinstance(val, schema):
                return val
            if isinstance(val, dict):
                return schema(**val)
            if callable(val):
                res = val(text)
                return res if isinstance(res, schema) else schema(**res)

        # Default fallback creation if no canned data registered
        raise LLMProviderError(f"No mock response configured for schema {schema_name}")

    def summarize_one_line(self, text: str) -> str:
        self.call_history.append({"method": "summarize_one_line", "text": text})
        if self.should_raise:
            raise LLMProviderError("Mock LLM error")
        return self.canned_summaries.get(text, f"Incident Summary: {text[:40]}...")

    def generate_text(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        self.call_history.append({"method": "generate_text", "prompt": prompt, "system_prompt": system_prompt})
        if self.should_raise:
            raise LLMProviderError("Mock LLM error")
        return self.canned_text or "Based on nearby well records [source: doc_1], loss circulation was encountered."


class HeuristicLLMProvider:
    """Heuristic fallback LLM provider for development / offline environments.
    Extracts structured fields using deterministic rule-based analysis."""

    def extract_structured(self, prompt: str, schema: Type[T], text: str) -> T:
        schema_name = schema.__name__

        if schema_name == "WellMetadataSchema":
            well_match = re.search(r"(?:Well|Well Name|Well No)[:#\-]?\s*([A-Za-z0-9_\-]+)", text, re.IGNORECASE)
            well_name = well_match.group(1).strip() if well_match else "UNKNOWN_WELL"
            op_match = re.search(r"Operator[:#\-]?\s*([A-Za-z0-9\s,\.]+?)(?=\n|Well|Date|Field|$)", text, re.IGNORECASE)
            operator = op_match.group(1).strip() if op_match else "Oil India Limited"
            data = {"well_name": well_name, "spud_date": None, "operator": operator, "confidence": 0.85}
            return schema(**data)

        if schema_name == "DepthEventSchema":
            depth_match = re.search(r"(\d{3,5}(?:\.\d+)?)\s*m\b", text)
            depth = float(depth_match.group(1)) if depth_match else 1000.0

            # Try to match event type from text
            detected_type = EventType.OTHER
            for synonym, ev_type in from_synonyms(text):
                detected_type = ev_type
                break

            data = {
                "event_type": detected_type,
                "depth": depth,
                "description": text.strip()[:500],
                "confidence": 0.85,
            }
            return schema(**data)

        if schema_name == "FormationSchema":
            name_match = re.search(r"(?:Formation|Fm)[:#\-]?\s*([A-Za-z0-9\s]+?)(?=\s*(?:top|from|depth|\d))", text, re.IGNORECASE)
            fname = name_match.group(1).strip() if name_match else "Barail"
            depths = [float(m) for m in re.findall(r"\b(\d{3,5}(?:\.\d+)?)\s*m\b", text)]
            top = depths[0] if depths else 1000.0
            bottom = depths[1] if len(depths) > 1 else top + 200.0
            if bottom <= top:
                bottom = top + 100.0
            data = {"formation_name": fname, "top_depth": top, "bottom_depth": bottom, "confidence": 0.85}
            return schema(**data)

        if schema_name == "IncidentSchema":
            depth_match = re.search(r"(\d{3,5}(?:\.\d+)?)\s*m\b", text)
            depth = float(depth_match.group(1)) if depth_match else None
            data = {
                "cause": "Mechanical failure or pressure differential",
                "depth": depth,
                "mitigation": "Pumped LCM pill and circulated",
                "outcome": "Circulation restored",
                "description": text.strip()[:500],
                "confidence": 0.85,
            }
            return schema(**data)

        raise LLMProviderError(f"Unsupported schema {schema_name}")

    def summarize_one_line(self, text: str) -> str:
        words = text.strip().split()
        summary = " ".join(words[:8])
        return f"{summary}..." if len(words) > 8 else summary

    def generate_text(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        return "Based on offset well records, nearby wells experienced incidents in this formation."


def from_synonyms(text: str):
    lower = text.lower()
    from domain.models.enums import EVENT_TYPE_SYNONYMS
    for phrase, ev_type in EVENT_TYPE_SYNONYMS.items():
        if phrase in lower:
            yield phrase, ev_type
