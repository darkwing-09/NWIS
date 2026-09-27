from pathlib import Path
import fitz  # PyMuPDF

from config.errors import CorruptFileError
from domain.models.ocr import DocumentClass


def classify_document(file_path: str | Path) -> DocumentClass:
    """
    Determine document type (WCR/DDR) and page count before processing.
    Raises CorruptFileError on unreadable or invalid PDF files.
    """
    path_str = str(file_path)
    try:
        doc = fitz.open(path_str)
    except Exception as exc:
        raise CorruptFileError(f"Failed to open PDF document: {exc}", detail={"file_path": path_str}) from exc

    try:
        page_count = len(doc)
        if page_count == 0:
            raise CorruptFileError("PDF contains 0 pages", detail={"file_path": path_str})

        # Scan text on the first page
        first_page = doc[0]
        text = (first_page.get_text() or "").lower()

        if "daily drilling" in text or "ddr" in text or "daily report" in text:
            doc_type = "DDR"
        else:
            doc_type = "WCR"

        return DocumentClass(doc_type=doc_type, page_count=page_count)
    except CorruptFileError:
        raise
    except Exception as exc:
        raise CorruptFileError(f"Corrupt or unreadable PDF: {exc}", detail={"file_path": path_str}) from exc
    finally:
        doc.close()
