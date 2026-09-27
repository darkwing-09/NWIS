import uuid
from pathlib import Path
import fitz  # PyMuPDF
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.worker.ocr_worker import OCRWorker
from config.errors import CorruptFileError, OCRProcessingError
from database.models.base import Base
from database.models.documents import Document, DocumentPage
from domain.events.publisher import clear_published_events, get_published_events
from domain.events.schemas import DocumentUploaded, DocumentUploadedPayload
from domain.models.ocr import OCRPageResult
from infrastructure.storage.client import LocalStorageClient, set_storage_client
from services.ocr.classifier import classify_document
from services.ocr.confidence import calculate_document_confidence
from services.ocr.native_extractor import extract_native_text
from services.ocr.normalizer import normalize_units, reconstruct_pages, strip_headers_footers
from services.ocr.ocr_engine import MockOCREngine, ocr_pages, set_ocr_engine
from services.ocr.service import process_document
from services.ocr.text_layer import detect_text_layer


def create_test_pdf(pages_text: list[str]) -> bytes:
    """Helper to generate a PDF with native digital text layer per page."""
    doc = fitz.open()
    for text in pages_text:
        page = doc.new_page()
        page.insert_text((50, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def create_scanned_test_pdf(page_count: int = 1) -> bytes:
    """Helper to generate a scanned PDF containing image rasters without a digital text layer."""
    doc = fitz.open()
    for _ in range(page_count):
        page = doc.new_page()
        pix = fitz.Pixmap(fitz.csRGB, (0, 0, 100, 100), 1)
        page.insert_image(page.rect, pixmap=pix)
    data = doc.tobytes()
    doc.close()
    return data


def create_mixed_test_pdf() -> bytes:
    """Helper to generate a 2-page PDF: Page 0 has text layer, Page 1 is scanned."""
    doc = fitz.open()
    # Page 0: Native text with > 100 characters
    p1 = doc.new_page()
    p1.insert_text(
        (50, 72),
        "OIL INDIA LIMITED - WELL COMPLETION REPORT\n"
        "Well Name: NH-01 Field: DIBRUGARH Total Depth: 3500 mtrs\n"
        "Formation Top: Barail at 2800 mtrs. Casing set at 3200 mtrs.\n"
        "This page contains substantial digital text exceeding the threshold limit.",
    )
    # Page 1: Scanned image with no text
    p2 = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, (0, 0, 100, 100), 1)
    p2.insert_image(p2.rect, pixmap=pix)

    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def ocr_test_env(tmp_path):
    storage_dir = tmp_path / "ocr_storage"
    storage_client = LocalStorageClient(base_dir=storage_dir)
    set_storage_client(storage_client)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    mock_engine = MockOCREngine(default_confidence=0.92, default_text="Scanned OCR text extracted")
    set_ocr_engine(mock_engine)
    clear_published_events()

    with Session(engine) as session:
        yield session, engine, tmp_path, mock_engine, storage_client

    clear_published_events()


# ---------------------------------------------------------
# 05.1 Classifier Tests
# ---------------------------------------------------------
def test_classify_wcr(tmp_path):
    pdf_bytes = create_test_pdf(["OIL INDIA LIMITED\nWELL COMPLETION REPORT\nWell: NH-01"])
    fpath = tmp_path / "wcr.pdf"
    fpath.write_bytes(pdf_bytes)

    res = classify_document(fpath)
    assert res.doc_type == "WCR"
    assert res.page_count == 1


def test_classify_ddr(tmp_path):
    pdf_bytes = create_test_pdf(["DAILY DRILLING REPORT\nDate: 2026-03-01\nOperation: Drilling"])
    fpath = tmp_path / "ddr.pdf"
    fpath.write_bytes(pdf_bytes)

    res = classify_document(fpath)
    assert res.doc_type == "DDR"
    assert res.page_count == 1


def test_classify_corrupt_file_raises(tmp_path):
    fpath = tmp_path / "corrupt.pdf"
    fpath.write_bytes(b"NOT A VALID PDF FILE AT ALL")

    with pytest.raises(CorruptFileError):
        classify_document(fpath)


# ---------------------------------------------------------
# 05.2 Text Layer Detection Tests
# ---------------------------------------------------------
def test_detects_native_pdf(tmp_path):
    long_text = "Oil India Limited WCR Report " * 15  # > 100 chars
    pdf_bytes = create_test_pdf([long_text])
    fpath = tmp_path / "native.pdf"
    fpath.write_bytes(pdf_bytes)

    res = detect_text_layer(fpath)
    assert res.page_flags == [True]


def test_detects_scanned_pdf(tmp_path):
    pdf_bytes = create_scanned_test_pdf(2)
    fpath = tmp_path / "scanned.pdf"
    fpath.write_bytes(pdf_bytes)

    res = detect_text_layer(fpath)
    assert res.page_flags == [False, False]


def test_mixed_document(tmp_path):
    pdf_bytes = create_mixed_test_pdf()
    fpath = tmp_path / "mixed.pdf"
    fpath.write_bytes(pdf_bytes)

    res = detect_text_layer(fpath)
    assert res.page_flags == [True, False]


# ---------------------------------------------------------
# 05.3 Native Extractor Tests
# ---------------------------------------------------------
def test_extract_native_pages(tmp_path):
    pdf_bytes = create_test_pdf(["Page 1 Content Here", "Page 2 Content Here"])
    fpath = tmp_path / "multi.pdf"
    fpath.write_bytes(pdf_bytes)

    extracted = extract_native_text(fpath, [0, 1])
    assert "Page 1 Content Here" in extracted[0]
    assert "Page 2 Content Here" in extracted[1]


def test_partial_failure_isolated(tmp_path):
    pdf_bytes = create_test_pdf(["Single Page Document"])
    fpath = tmp_path / "single.pdf"
    fpath.write_bytes(pdf_bytes)

    # Index 5 is out of bounds
    extracted = extract_native_text(fpath, [0, 5])
    assert "Single Page Document" in extracted[0]
    assert extracted[5] == ""


# ---------------------------------------------------------
# 05.4–05.6 OCR Engine & Confidence Tests
# ---------------------------------------------------------
def test_ocr_clean_scan(tmp_path):
    pdf_bytes = create_scanned_test_pdf(1)
    fpath = tmp_path / "scan.pdf"
    fpath.write_bytes(pdf_bytes)

    engine = MockOCREngine(default_confidence=0.95, default_text="Recovered text")
    set_ocr_engine(engine)

    res = ocr_pages(fpath, [0])
    assert 0 in res
    assert res[0].confidence == 0.95
    assert res[0].text == "Recovered text"


def test_ocr_low_quality_scan_low_confidence(tmp_path):
    pdf_bytes = create_scanned_test_pdf(1)
    fpath = tmp_path / "scan.pdf"
    fpath.write_bytes(pdf_bytes)

    engine = MockOCREngine(default_confidence=0.35, default_text="Degraded fuzzy characters")
    set_ocr_engine(engine)

    res = ocr_pages(fpath, [0])
    assert res[0].confidence == 0.35


def test_ocr_engine_failure_raises(tmp_path):
    pdf_bytes = create_scanned_test_pdf(1)
    fpath = tmp_path / "scan.pdf"
    fpath.write_bytes(pdf_bytes)

    engine = MockOCREngine()
    engine.should_fail = True
    set_ocr_engine(engine)

    with pytest.raises(OCRProcessingError):
        ocr_pages(fpath, [0])


def test_confidence_rollup():
    confidences = {0: 1.0, 1: 0.8, 2: 0.4}
    texts = {
        0: "A" * 100,  # weight 100
        1: "B" * 100,  # weight 100
        2: "C" * 100,  # weight 100
    }
    # Equal weights -> mean = (1.0 + 0.8 + 0.4) / 3 = 0.7333
    overall_conf, low_pages = calculate_document_confidence(confidences, texts)
    assert overall_conf == 0.7333
    assert low_pages == [2]


def test_single_bad_page_flagged():
    confidences = {0: 0.95, 1: 0.30}
    texts = {0: "Healthy page text", 1: "Bad page"}
    overall_conf, low_pages = calculate_document_confidence(confidences, texts)
    assert 1 in low_pages
    assert 0 not in low_pages


# ---------------------------------------------------------
# 05.7–05.9 Normalization Tests
# ---------------------------------------------------------
def test_reconstruct_order_preserved():
    native = {0: "Page 0 Native", 2: "Page 2 Native"}
    ocr = {1: OCRPageResult(page_index=1, text="Page 1 Scanned", confidence=0.9)}

    ordered = reconstruct_pages(native, ocr, total_pages=3)
    assert len(ordered) == 3
    assert ordered[0] == "Page 0 Native"
    assert ordered[1] == "Page 1 Scanned"
    assert ordered[2] == "Page 2 Native"


def test_header_strip_removes_repeated_line():
    header = "OIL INDIA LIMITED - CONFIDENTIAL DRILLING REPORT"
    pages = [
        f"{header}\nWell NH-01 daily log page 1",
        f"{header}\nWell NH-01 daily log page 2",
        f"{header}\nWell NH-01 daily log page 3",
    ]
    cleaned = strip_headers_footers(pages)
    for p in cleaned:
        assert header not in p
        assert "Well NH-01 daily log" in p


def test_header_strip_keeps_unique_line():
    pages = [
        "Common header\nUnique line in page 1",
        "Common header\nUnique line in page 2",
        "Unique line in page 3 without common header",
    ]
    cleaned = strip_headers_footers(pages)
    assert "Unique line in page 1" in cleaned[0]
    assert "Unique line in page 2" in cleaned[1]
    assert "Unique line in page 3" in cleaned[2]


def test_unit_normalization_variants():
    raw_text = "Drilled to 2500 mtrs, then encountered gas at 2600 metres and 2700 MTRS. Mud pressure 3000 PSI."
    normalized = normalize_units(raw_text)
    assert "2500 m" in normalized
    assert "2600 m" in normalized
    assert "2700 m" in normalized
    assert "3000 psi" in normalized


# ---------------------------------------------------------
# 05.10–05.12 Service & Acceptance Criteria Tests
# ---------------------------------------------------------
def test_process_native_pdf(ocr_test_env):
    session, _, _, mock_engine, storage = ocr_test_env
    pdf_bytes = create_test_pdf([
        "OIL INDIA LIMITED - WELL COMPLETION REPORT\n"
        "Well: NH-01\n"
        "Total Depth: 3400 mtrs. Barail formation from 2800 mtrs to 3100 mtrs."
    ])
    doc_id = uuid.uuid4()
    key = f"wells/{doc_id}.pdf"
    storage.put_object(pdf_bytes, key)

    doc = Document(
        doc_id=doc_id,
        file_path=key,
        doc_type="WCR",
        ocr_status="pending",
        extraction_status="pending",
    )
    session.add(doc)
    session.commit()

    # Track if mock_engine was called
    mock_engine.page_overrides.clear()
    engine_called = False

    def tracked_ocr(img, idx):
        nonlocal engine_called
        engine_called = True
        return OCRPageResult(page_index=idx, text="should not be called", confidence=1.0)

    mock_engine.ocr_page = tracked_ocr

    res = process_document(doc_id, db=session)

    # Acceptance criterion: Document with text layer never invokes Tesseract
    assert engine_called is False
    assert res.confidence == 1.0
    assert "3400 m" in res.text

    # Verify DB updated
    session.refresh(doc)
    assert doc.ocr_status == "done"
    assert doc.confidence == 1.0

    # Verify pages persisted
    pages = session.query(DocumentPage).filter_by(doc_id=doc_id).all()
    assert len(pages) == 1
    assert pages[0].has_text_layer is True

    # Verify event emitted
    events = get_published_events()
    assert any(e.event_type == "DocumentOCRCompleted" for e in events)


def test_process_scanned_pdf(ocr_test_env):
    session, _, _, mock_engine, storage = ocr_test_env
    pdf_bytes = create_scanned_test_pdf(2)
    doc_id = uuid.uuid4()
    key = f"wells/{doc_id}.pdf"
    storage.put_object(pdf_bytes, key)

    doc = Document(
        doc_id=doc_id,
        file_path=key,
        doc_type="WCR",
        ocr_status="pending",
        extraction_status="pending",
    )
    session.add(doc)
    session.commit()

    mock_engine.page_overrides = {
        0: OCRPageResult(page_index=0, text="Scanned Page 1 OCR Result at 2850 mtrs", confidence=0.88),
        1: OCRPageResult(page_index=1, text="Scanned Page 2 OCR Result at 2950 mtrs", confidence=0.88),
    }

    res = process_document(doc_id, db=session)

    # Acceptance criterion: Scanned document always has confidence score persisted
    assert res.confidence == 0.88
    assert "2850 m" in res.text

    session.refresh(doc)
    assert doc.ocr_status == "done"
    assert doc.confidence == 0.88

    pages = session.query(DocumentPage).filter_by(doc_id=doc_id).all()
    assert len(pages) == 2
    assert all(p.has_text_layer is False for p in pages)


def test_process_mixed_pdf(ocr_test_env):
    session, _, _, mock_engine, storage = ocr_test_env
    pdf_bytes = create_mixed_test_pdf()
    doc_id = uuid.uuid4()
    key = f"wells/{doc_id}.pdf"
    storage.put_object(pdf_bytes, key)

    doc = Document(
        doc_id=doc_id,
        file_path=key,
        doc_type="WCR",
        ocr_status="pending",
        extraction_status="pending",
    )
    session.add(doc)
    session.commit()

    mock_engine.default_confidence = 0.70
    mock_engine.default_text = "Scanned page 2 content"

    res = process_document(doc_id, db=session)

    session.refresh(doc)
    assert doc.ocr_status == "done"
    pages = session.query(DocumentPage).filter_by(doc_id=doc_id).order_by(DocumentPage.page_number).all()
    assert len(pages) == 2
    assert pages[0].has_text_layer is True
    assert pages[1].has_text_layer is False


def test_low_confidence_document_flagged(ocr_test_env):
    session, _, _, mock_engine, storage = ocr_test_env
    pdf_bytes = create_scanned_test_pdf(1)
    doc_id = uuid.uuid4()
    key = f"wells/{doc_id}.pdf"
    storage.put_object(pdf_bytes, key)

    doc = Document(
        doc_id=doc_id,
        file_path=key,
        doc_type="WCR",
        ocr_status="pending",
        extraction_status="pending",
    )
    session.add(doc)
    session.commit()

    mock_engine.default_confidence = 0.35  # Below floor 0.5
    res = process_document(doc_id, db=session)

    assert 0 in res.low_confidence_pages


def test_ocr_failure_marks_failed_status(ocr_test_env):
    session, _, _, mock_engine, storage = ocr_test_env
    pdf_bytes = create_scanned_test_pdf(1)
    doc_id = uuid.uuid4()
    key = f"wells/{doc_id}.pdf"
    storage.put_object(pdf_bytes, key)

    doc = Document(
        doc_id=doc_id,
        file_path=key,
        doc_type="WCR",
        ocr_status="pending",
        extraction_status="pending",
    )
    session.add(doc)
    session.commit()

    mock_engine.should_fail = True

    with pytest.raises(OCRProcessingError):
        process_document(doc_id, db=session)

    # Acceptance criterion: failure never leaves status='pending' indefinitely
    session.refresh(doc)
    assert doc.ocr_status == "failed"


# ---------------------------------------------------------
# Worker Queue Consumer Tests
# ---------------------------------------------------------
def test_worker_acks_on_success(ocr_test_env):
    session, _, _, _, storage = ocr_test_env
    pdf_bytes = create_test_pdf(["Valid Digital WCR Document " * 10])
    doc_id = uuid.uuid4()
    key = f"wells/{doc_id}.pdf"
    storage.put_object(pdf_bytes, key)

    doc = Document(
        doc_id=doc_id,
        file_path=key,
        doc_type="WCR",
        ocr_status="pending",
        extraction_status="pending",
    )
    session.add(doc)
    session.commit()

    worker = OCRWorker(max_attempts=3)
    event = DocumentUploaded(
        payload=DocumentUploadedPayload(
            document_id=doc_id,
            well_id=uuid.uuid4(),
            file_path=key,
        )
    )

    ack = worker.handle_document_uploaded(event, db=session)
    assert ack is True
    assert len(worker.dlq) == 0


def test_worker_retries_on_transient_failure(ocr_test_env):
    session, _, _, mock_engine, storage = ocr_test_env
    pdf_bytes = create_scanned_test_pdf(1)
    doc_id = uuid.uuid4()
    key = f"wells/{doc_id}.pdf"
    storage.put_object(pdf_bytes, key)

    doc = Document(
        doc_id=doc_id,
        file_path=key,
        doc_type="WCR",
        ocr_status="pending",
        extraction_status="pending",
    )
    session.add(doc)
    session.commit()

    mock_engine.should_fail = True
    worker = OCRWorker(max_attempts=3)
    event = DocumentUploaded(
        payload=DocumentUploadedPayload(
            document_id=doc_id,
            well_id=uuid.uuid4(),
            file_path=key,
        )
    )

    # First attempt fails -> Nack for retry
    ack1 = worker.handle_document_uploaded(event, db=session)
    assert ack1 is False
    assert len(worker.dlq) == 0
    assert worker.attempt_counts[str(doc_id)] == 1


def test_worker_routes_to_dlq_after_max_attempts(ocr_test_env):
    session, _, _, mock_engine, storage = ocr_test_env
    pdf_bytes = create_scanned_test_pdf(1)
    doc_id = uuid.uuid4()
    key = f"wells/{doc_id}.pdf"
    storage.put_object(pdf_bytes, key)

    doc = Document(
        doc_id=doc_id,
        file_path=key,
        doc_type="WCR",
        ocr_status="pending",
        extraction_status="pending",
    )
    session.add(doc)
    session.commit()

    mock_engine.should_fail = True
    worker = OCRWorker(max_attempts=3)
    event = DocumentUploaded(
        payload=DocumentUploadedPayload(
            document_id=doc_id,
            well_id=uuid.uuid4(),
            file_path=key,
        )
    )

    # Attempt 1
    worker.handle_document_uploaded(event, db=session)
    # Attempt 2
    worker.handle_document_uploaded(event, db=session)
    # Attempt 3 (reaches max_attempts)
    ack3 = worker.handle_document_uploaded(event, db=session)

    assert ack3 is False
    assert len(worker.dlq) == 1
    assert worker.dlq[0]["attempts"] == 3
    assert "Simulated OCR failure" in worker.dlq[0]["error"]
