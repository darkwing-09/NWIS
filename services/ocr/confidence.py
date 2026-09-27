from typing import Dict, List, Tuple
from config.settings import get_settings


def calculate_document_confidence(
    page_confidences: Dict[int, float],
    page_texts: Dict[int, str],
) -> Tuple[float, List[int]]:
    """
    Roll up page confidences to a single document-level score weighted by page text length.
    Pages below NWIS_PAGE_CONFIDENCE_FLOOR are flagged in low_confidence_pages.
    """
    settings = get_settings()
    confidence_floor = settings.ocr_page_confidence_floor

    if not page_confidences:
        return 1.0, []

    total_weight = 0
    weighted_confidence_sum = 0.0
    low_confidence_pages: List[int] = []

    for page_idx, conf in page_confidences.items():
        if conf < confidence_floor:
            low_confidence_pages.append(page_idx)

        text = page_texts.get(page_idx, "")
        weight = len(text.strip())
        if weight == 0:
            weight = 1  # Minimum nominal weight for blank/scanned empty pages

        total_weight += weight
        weighted_confidence_sum += conf * weight

    document_confidence = weighted_confidence_sum / total_weight if total_weight > 0 else 1.0

    return round(document_confidence, 4), sorted(low_confidence_pages)
