from pathlib import Path
import fitz  # PyMuPDF

from config.errors import CorruptFileError
from config.settings import get_settings
from domain.models.ocr import TextLayerResult


def detect_text_layer(file_path: str | Path) -> TextLayerResult:
    """
    Decide native-extraction vs OCR path per page.
    Pages with fewer than NWIS_TEXT_LAYER_THRESHOLD chars are flagged as scanned (False).
    """
    path_str = str(file_path)
    settings = get_settings()
    threshold = settings.ocr_text_layer_threshold_chars

    try:
        doc = fitz.open(path_str)
    except Exception as exc:
        raise CorruptFileError(f"Failed to open PDF document: {exc}", detail={"file_path": path_str}) from exc

    page_flags = []
    try:
        if len(doc) == 0:
            raise CorruptFileError("PDF contains 0 pages", detail={"file_path": path_str})

        for page in doc:
            text = page.get_text() or ""
            # Check length of stripped non-whitespace characters
            char_count = len(text.strip())
            has_text = char_count >= threshold
            page_flags.append(has_text)

        return TextLayerResult(page_flags=page_flags)
    except CorruptFileError:
        raise
    except Exception as exc:
        raise CorruptFileError(f"Error inspecting PDF text layer: {exc}", detail={"file_path": path_str}) from exc
    finally:
        doc.close()
