import io
import time
import uuid
from datetime import datetime, timezone
import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.api.main import create_app
from config.errors import (
    AuthorizationError,
    FileTooLargeError,
    NotFoundError,
    UnsupportedFileTypeError,
    VirusScanFailedError,
    WellNotFoundError,
)
from database.models.base import Base
from database.models.documents import Document, Extraction, ExtractionError
from database.models.wells import Well
from database.session import get_session
from domain.events.publisher import clear_published_events, get_published_events
from domain.models.auth import AuthenticatedUser
from infrastructure.storage.client import (
    LocalStorageClient,
    get_object,
    get_presigned_url,
    put_object,
    set_storage_client,
)
from services.auth.oidc_client import create_token
from services.documents.service import get_document, get_document_status, upload_document
from services.documents.validation import validate_upload

VALID_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
INFECTED_PDF_BYTES = b"%PDF-1.4\nEICAR-STANDARD-ANTIVIRUS-TEST-FILE\n%%EOF"


@pytest.fixture
def doc_db(tmp_path):
    storage_dir = tmp_path / "storage"
    set_storage_client(LocalStorageClient(base_dir=storage_dir))

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session, engine


@pytest.fixture(autouse=True)
def clean_events():
    clear_published_events()
    yield
    clear_published_events()


# ---------------------------------------------------------
# Storage client tests
# ---------------------------------------------------------
def test_put_get_roundtrip(tmp_path):
    storage = LocalStorageClient(base_dir=tmp_path / "test_store")
    key = "wells/sample.pdf"
    storage.put_object(VALID_PDF_BYTES, key)

    retrieved = storage.get_object(key)
    assert retrieved == VALID_PDF_BYTES


def test_presigned_url_expires(tmp_path):
    storage = LocalStorageClient(base_dir=tmp_path / "test_store")
    key = "wells/sample.pdf"
    storage.put_object(VALID_PDF_BYTES, key)

    # Valid URL
    valid_url = storage.get_presigned_url(key, expiry_sec=300)
    expires_at = int(valid_url.split("expires=")[1].split("&")[0])
    sig = valid_url.split("signature=")[1]
    assert storage.verify_presigned_url(key, expires_at, sig) is True

    # Expired URL (past timestamp)
    expired_url = storage.get_presigned_url(key, expiry_sec=-10)
    exp_expires_at = int(expired_url.split("expires=")[1].split("&")[0])
    exp_sig = expired_url.split("signature=")[1]
    assert storage.verify_presigned_url(key, exp_expires_at, exp_sig) is False


# ---------------------------------------------------------
# Validation tests
# ---------------------------------------------------------
def test_rejects_non_pdf_by_content():
    file = UploadFile(
        file=io.BytesIO(b"NOT A PDF DOCUMENT"),
        filename="report.txt",
        headers={"content-type": "text/plain"},
    )
    with pytest.raises(UnsupportedFileTypeError):
        validate_upload(file)


def test_rejects_fake_extension_non_pdf():
    # Has .pdf extension but binary content is not PDF
    file = UploadFile(
        file=io.BytesIO(b"FAKE CONTENT NOT PDF"),
        filename="report.pdf",
        headers={"content-type": "application/pdf"},
    )
    with pytest.raises(UnsupportedFileTypeError, match="missing standard PDF header"):
        validate_upload(file)


def test_rejects_oversized_file():
    # 2MB file validated against 1MB max
    oversized = b"%PDF-1.4\n" + b"X" * (2 * 1024 * 1024)
    file = UploadFile(
        file=io.BytesIO(oversized),
        filename="large.pdf",
        headers={"content-type": "application/pdf"},
    )
    with pytest.raises(FileTooLargeError):
        validate_upload(file, max_size_mb=1)


def test_rejects_infected_file_mocked_scanner():
    file = UploadFile(
        file=io.BytesIO(INFECTED_PDF_BYTES),
        filename="virus.pdf",
        headers={"content-type": "application/pdf"},
    )
    with pytest.raises(VirusScanFailedError):
        validate_upload(file)


def test_accepts_valid_pdf():
    file = UploadFile(
        file=io.BytesIO(VALID_PDF_BYTES),
        filename="valid_wcr.pdf",
        headers={"content-type": "application/pdf"},
    )
    # Should not raise
    validate_upload(file)


# ---------------------------------------------------------
# Document service tests
# ---------------------------------------------------------
def test_upload_happy_path(doc_db):
    session, _ = doc_db
    well_id = uuid.uuid4()
    well = Well(
        well_id=well_id,
        name="NH-01",
        field_name="DIBRUGARH",
        latitude=27.4,
        longitude=94.9,
        spud_date=datetime.now(timezone.utc).date(),
        status="active",
    )
    session.add(well)
    session.commit()

    user = AuthenticatedUser(
        user_id=uuid.uuid4(),
        email="eng@oilindia.in",
        roles=["engineer"],
        allowed_fields=["DIBRUGARH"],
    )

    file = UploadFile(
        file=io.BytesIO(VALID_PDF_BYTES),
        filename="well_completion_report.pdf",
        headers={"content-type": "application/pdf"},
    )

    doc = upload_document(file=file, well_id=well_id, user=user, db=session)

    assert doc.doc_id is not None
    assert doc.well_id == well_id
    assert doc.doc_type == "WCR"
    assert doc.ocr_status == "pending"
    assert doc.extraction_status == "pending"
    assert doc.data_classification == "internal"

    # Verify object persisted in storage
    stored = get_object(doc.file_path)
    assert stored == VALID_PDF_BYTES


def test_upload_amendment_links_to_original(doc_db):
    session, _ = doc_db
    well_id = uuid.uuid4()
    well = Well(
        well_id=well_id,
        name="NH-01",
        field_name="DIBRUGARH",
        latitude=27.4,
        longitude=94.9,
        spud_date=datetime.now(timezone.utc).date(),
        status="active",
    )
    session.add(well)
    session.commit()

    user = AuthenticatedUser(
        user_id=uuid.uuid4(),
        email="eng@oilindia.in",
        roles=["engineer"],
        allowed_fields=["DIBRUGARH"],
    )

    # Upload original
    file1 = UploadFile(file=io.BytesIO(VALID_PDF_BYTES), filename="orig.pdf")
    doc1 = upload_document(file1, well_id, user, session)

    # Upload amendment
    file2 = UploadFile(file=io.BytesIO(VALID_PDF_BYTES), filename="amended.pdf")
    doc2 = upload_document(file2, well_id, user, session, amends_document_id=doc1.doc_id)

    assert doc2.amends_document_id == doc1.doc_id


def test_upload_out_of_scope_well_denied(doc_db):
    session, _ = doc_db
    well_id = uuid.uuid4()
    well = Well(
        well_id=well_id,
        name="DULIAJAN-01",
        field_name="DULIAJAN",
        latitude=27.3,
        longitude=95.3,
        spud_date=datetime.now(timezone.utc).date(),
        status="active",
    )
    session.add(well)
    session.commit()

    user = AuthenticatedUser(
        user_id=uuid.uuid4(),
        email="eng@oilindia.in",
        roles=["engineer"],
        allowed_fields=["DIBRUGARH"],  # No access to DULIAJAN
    )

    file = UploadFile(file=io.BytesIO(VALID_PDF_BYTES), filename="wcr.pdf")
    with pytest.raises(AuthorizationError):
        upload_document(file, well_id, user, session)


def test_upload_emits_event(doc_db):
    session, _ = doc_db
    well_id = uuid.uuid4()
    well = Well(
        well_id=well_id,
        name="NH-01",
        field_name="DIBRUGARH",
        latitude=27.4,
        longitude=94.9,
        spud_date=datetime.now(timezone.utc).date(),
        status="active",
    )
    session.add(well)
    session.commit()

    user = AuthenticatedUser(
        user_id=uuid.uuid4(),
        email="eng@oilindia.in",
        roles=["engineer"],
        allowed_fields=["DIBRUGARH"],
    )

    file = UploadFile(file=io.BytesIO(VALID_PDF_BYTES), filename="ddr_daily.pdf")
    doc = upload_document(file, well_id, user, session)

    events = get_published_events()
    assert len(events) == 1
    assert events[0].event_type == "DocumentUploaded"
    assert events[0].payload.document_id == doc.doc_id
    assert events[0].payload.well_id == well_id


def test_get_document_scoped_by_field(doc_db):
    session, _ = doc_db
    well_id = uuid.uuid4()
    well = Well(
        well_id=well_id,
        name="NH-01",
        field_name="DIBRUGARH",
        latitude=27.4,
        longitude=94.9,
        spud_date=datetime.now(timezone.utc).date(),
        status="active",
    )
    doc_id = uuid.uuid4()
    doc = Document(
        doc_id=doc_id,
        well_id=well_id,
        doc_type="WCR",
        file_path="wells/doc.pdf",
        ocr_status="done",
        extraction_status="done",
    )
    session.add(well)
    session.add(doc)
    session.commit()

    allowed_user = AuthenticatedUser(
        user_id=uuid.uuid4(),
        email="eng@oilindia.in",
        roles=["engineer"],
        allowed_fields=["DIBRUGARH"],
    )
    denied_user = AuthenticatedUser(
        user_id=uuid.uuid4(),
        email="other@oilindia.in",
        roles=["engineer"],
        allowed_fields=["JORHAT"],
    )

    result = get_document(doc_id, allowed_user, session)
    assert result.doc_id == doc_id
    assert result.ocr_status == "done"

    with pytest.raises(AuthorizationError):
        get_document(doc_id, denied_user, session)


def test_status_reflects_pipeline_stage(doc_db):
    session, _ = doc_db
    well_id = uuid.uuid4()
    well = Well(
        well_id=well_id,
        name="NH-01",
        field_name="DIBRUGARH",
        latitude=27.4,
        longitude=94.9,
        spud_date=datetime.now(timezone.utc).date(),
        status="active",
    )
    doc_id = uuid.uuid4()
    doc = Document(
        doc_id=doc_id,
        well_id=well_id,
        doc_type="WCR",
        file_path="wells/doc.pdf",
        ocr_status="done",
        extraction_status="needs_review",
    )
    ext_id = uuid.uuid4()
    extraction = Extraction(
        extraction_id=ext_id,
        doc_id=doc_id,
        extractor_name="depth_events",
        raw_output={"events": []},
        status="failed",
    )
    err1 = ExtractionError(
        error_id=uuid.uuid4(),
        extraction_id=ext_id,
        error_detail="Depth out of range",
    )
    err2 = ExtractionError(
        error_id=uuid.uuid4(),
        extraction_id=ext_id,
        error_detail="Unknown formation",
    )
    session.add_all([well, doc, extraction, err1, err2])
    session.commit()

    user = AuthenticatedUser(
        user_id=uuid.uuid4(),
        email="eng@oilindia.in",
        roles=["engineer"],
        allowed_fields=["DIBRUGARH"],
    )

    status_res = get_document_status(doc_id, user, session)
    assert status_res.doc_id == doc_id
    assert status_res.ocr_status == "done"
    assert status_res.extraction_status == "needs_review"
    assert status_res.needs_review_count == 2


# ---------------------------------------------------------
# API Router tests
# ---------------------------------------------------------
def test_documents_api_upload_and_get(doc_db):
    session, engine = doc_db
    well_id = uuid.uuid4()
    well = Well(
        well_id=well_id,
        name="NH-01",
        field_name="DIBRUGARH",
        latitude=27.4,
        longitude=94.9,
        spud_date=datetime.now(timezone.utc).date(),
        status="active",
    )
    session.add(well)
    session.commit()

    app = create_app()

    def override_get_session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    token = create_token(
        sub="eng-1",
        email="eng@oilindia.in",
        name="Field Engineer",
        roles=["engineer"],
        allowed_fields=["DIBRUGARH"],
    )
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Upload WCR
    files = {"file": ("report.pdf", io.BytesIO(VALID_PDF_BYTES), "application/pdf")}
    data = {"well_id": str(well_id)}
    resp = client.post("/documents/upload", files=files, data=data, headers=headers)
    assert resp.status_code == 202
    resp_data = resp.json()
    assert "doc_id" in resp_data
    assert resp_data["status"] == "pending"
    doc_id = resp_data["doc_id"]

    # 2. Get document details
    get_resp = client.get(f"/documents/{doc_id}", headers=headers)
    assert get_resp.status_code == 200
    doc_data = get_resp.json()
    assert doc_data["doc_id"] == doc_id
    assert doc_data["well_id"] == str(well_id)

    # 3. Get document status
    status_resp = client.get(f"/documents/{doc_id}/status", headers=headers)
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["doc_id"] == doc_id
    assert status_data["ocr_status"] == "pending"
    assert status_data["needs_review_count"] == 0
