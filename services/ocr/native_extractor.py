from pathlib import Path
from typing import Dict, List
import fitz  # PyMuPDF

from domain.logging.logger import get_logger

logger = get_logger("services.ocr.native_extractor")


def extract_native_text(file_path: str | Path, page_indices: List[int]) -> Dict[int, str]:
    """
    Extract digital text for pages with an established text layer.
    Any single-page extraction error is isolated and does not abort the document.
    """
    path_str = str(file_path)
    results: Dict[int, str] = {}

    doc = fitz.open(path_str)
    try:
        total_pages = len(doc)
        for idx in page_indices:
            if idx < 0 or idx >= total_pages:
                logger.warning("Page index out of bounds", page_index=idx, total_pages=total_pages)
                results[idx] = ""
                continue
            try:
                page = doc[idx]
                text = page.get_text() or ""
                results[idx] = text
            except Exception as exc:
                logger.error("Failed to extract native text for page", page_index=idx, error=str(exc))
                results[idx] = ""
    finally:
        doc.close()

    return results
