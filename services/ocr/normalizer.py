import re
from collections import Counter
from typing import Dict, List, Optional

from config.settings import get_settings
from domain.models.ocr import OCRPageResult
from domain.models.units import ALL_UNIT_VARIANTS, UNIT_NORMALIZATION_REGEX


def reconstruct_pages(
    native: Dict[int, str],
    ocr: Dict[int, OCRPageResult],
    total_pages: Optional[int] = None,
) -> List[str]:
    """
    Merge native digital text and OCR-extracted text into a strictly ordered list of page strings.
    """
    all_indices = set(native.keys()) | set(ocr.keys())
    if total_pages is not None:
        count = total_pages
    elif all_indices:
        count = max(all_indices) + 1
    else:
        count = 0

    ordered_pages: List[str] = []
    for idx in range(count):
        if idx in native and native[idx].strip():
            ordered_pages.append(native[idx])
        elif idx in ocr:
            ordered_pages.append(ocr[idx].text)
        else:
            ordered_pages.append("")

    return ordered_pages


def strip_headers_footers(pages: List[str]) -> List[str]:
    """
    Remove lines repeating across >= NWIS_HEADER_REPEAT_THRESHOLD (default 60%) of pages.
    Guards unique content lines from being removed.
    """
    if len(pages) <= 1:
        return pages

    settings = get_settings()
    threshold_ratio = settings.ocr_header_repeat_threshold
    min_page_occurrences = max(2, int(len(pages) * threshold_ratio))

    # Count in how many distinct pages each trimmed line appears
    page_line_counter: Counter[str] = Counter()
    for page in pages:
        lines = set(line.strip() for line in page.splitlines() if line.strip())
        for line in lines:
            page_line_counter[line] += 1

    repeated_headers_footers = {
        line for line, count in page_line_counter.items()
        if count >= min_page_occurrences
    }

    cleaned_pages: List[str] = []
    for page in pages:
        cleaned_lines = [
            line for line in page.splitlines()
            if line.strip() not in repeated_headers_footers
        ]
        cleaned_pages.append("\n".join(cleaned_lines))

    return cleaned_pages


def normalize_units(text: str) -> str:
    """
    Canonicalize drilling and depth unit variations (e.g. 'mtrs', 'metres', 'MTRS' -> 'm')
    using the single-source table from domain/models/units.py.
    """
    if not text:
        return ""

    def replace_unit(match: re.Match) -> str:
        number = match.group(1)
        matched_unit = match.group(2)
        canonical = ALL_UNIT_VARIANTS.get(matched_unit) or ALL_UNIT_VARIANTS.get(matched_unit.lower(), matched_unit)
        return f"{number} {canonical}"

    return UNIT_NORMALIZATION_REGEX.sub(replace_unit, text)
