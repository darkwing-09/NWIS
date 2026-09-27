import io
from pathlib import Path
from typing import Dict, List, Optional, Protocol
import fitz  # PyMuPDF
from PIL import Image

from config.errors import OCRProcessingError
from domain.logging.logger import get_logger
from domain.models.ocr import OCRPageResult

logger = get_logger("services.ocr.ocr_engine")


class OCREngine(Protocol):
    def ocr_page(self, image: Image.Image, page_index: int) -> OCRPageResult:
        ...


class TesseractOCREngine:
    """Production OCR engine wrapping pytesseract."""

    def ocr_page(self, image: Image.Image, page_index: int) -> OCRPageResult:
        try:
            import pytesseract
            data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        except Exception as exc:
            logger.error("Tesseract execution failure", page_index=page_index, error=str(exc))
            raise OCRProcessingError(
                f"Tesseract OCR processing failed on page {page_index}: {exc}",
                detail={"page_index": page_index, "error": str(exc)},
            ) from exc

        words = []
        confidences = []
        n_boxes = len(data["text"])
        for i in range(n_boxes):
            word = data["text"][i].strip()
            conf_val = float(data["conf"][i])
            if word and conf_val >= 0:
                words.append(word)
                confidences.append(conf_val / 100.0)

        page_text = " ".join(words)
        page_conf = sum(confidences) / len(confidences) if confidences else 0.0

        return OCRPageResult(page_index=page_index, text=page_text, confidence=page_conf)


class MockOCREngine:
    """Mockable OCR engine for testing deterministic confidence and failure cases."""

    def __init__(self, default_confidence: float = 0.95, default_text: str = "OCR Extracted Text") -> None:
        self.default_confidence = default_confidence
        self.default_text = default_text
        self.should_fail = False
        self.page_overrides: Dict[int, OCRPageResult] = {}

    def ocr_page(self, image: Image.Image, page_index: int) -> OCRPageResult:
        if self.should_fail:
            raise OCRProcessingError(
                f"Simulated OCR failure on page {page_index}",
                detail={"page_index": page_index},
            )
        if page_index in self.page_overrides:
            return self.page_overrides[page_index]
        return OCRPageResult(
            page_index=page_index,
            text=self.default_text,
            confidence=self.default_confidence,
        )


_ocr_engine: OCREngine = TesseractOCREngine()


def get_ocr_engine() -> OCREngine:
    return _ocr_engine


def set_ocr_engine(engine: OCREngine) -> None:
    global _ocr_engine
    _ocr_engine = engine


def ocr_pages(file_path: str | Path, page_indices: List[int], dpi: int = 300) -> Dict[int, OCRPageResult]:
    """
    Rasterize specified scanned pages and perform OCR.
    """
    path_str = str(file_path)
    engine = get_ocr_engine()
    results: Dict[int, OCRPageResult] = {}

    try:
        doc = fitz.open(path_str)
    except Exception as exc:
        raise OCRProcessingError(f"Rasterization failure opening PDF: {exc}", detail={"file_path": path_str}) from exc

    try:
        total_pages = len(doc)
        for idx in page_indices:
            if idx < 0 or idx >= total_pages:
                logger.warning("Scanned page index out of range", page_index=idx, total_pages=total_pages)
                continue

            page = doc[idx]
            pix = page.get_pixmap(dpi=dpi)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

            page_result = engine.ocr_page(img, idx)
            results[idx] = page_result
    finally:
        doc.close()

    return results
