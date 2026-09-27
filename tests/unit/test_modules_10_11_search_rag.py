from datetime import datetime, timezone
import uuid
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.main import create_app
from config.errors import EmbeddingProviderError
from database.models.base import Base
from database.models.documents import Document, DocumentChunk
from database.models.wells import Well
from database.session import get_db
from domain.ai.embedding_provider import MockEmbeddingProvider
from domain.ai.llm_provider import MockLLMProvider
from domain.models.ertmac import WellContext
from services.auth.oidc_client import create_token
from services.ertmac.context_service import ContextService
from services.search.chunker import chunk_text
from services.search.embeddings import embed_chunks
from services.search.filters import apply_nearby_filter, exclude_superseded
from services.search.keyword_filter import apply_keyword_filter
from services.search.query_parser import parse_query
from services.search.rag import (
    RankedChunk,
    RawSynthesisResult,
    run_search,
    validate_citations,
)


@pytest.fixture
def in_memory_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def sample_well(in_memory_db):
    well = Well(
        well_id=uuid.uuid4(),
        name="NHK-050",
        status="active",
        latitude=27.35,
        longitude=95.32,
        field_name="Nahorkatiya",
        basin_name="Assam-Arakan",
    )
    in_memory_db.add(well)
    in_memory_db.commit()
    return well


@pytest.fixture
def sample_document(in_memory_db, sample_well):
    doc = Document(
        doc_id=uuid.uuid4(),
        well_id=sample_well.well_id,
        doc_type="WCR",
        file_path="uploads/nhk_050_wcr.pdf",
        ocr_status="completed",
        extraction_status="pending",
    )
    in_memory_db.add(doc)
    in_memory_db.commit()
    return doc


# --- 10.1 Chunker Tests ---

def test_chunks_respect_size_bound():
    text = "word " * 1200
    chunks = chunk_text(text, chunk_size=500, overlap=50)
    assert len(chunks) == 3
    for c in chunks:
        assert len(c.split()) <= 500


def test_overlap_preserved_between_chunks():
    words = [f"word_{i}" for i in range(100)]
    text = " ".join(words)
    chunks = chunk_text(text, chunk_size=50, overlap=10)
    assert len(chunks) >= 2
    # Check that the last words of chunk 0 appear in chunk 1
    last_words_chunk0 = chunks[0].split()[-10:]
    first_words_chunk1 = chunks[1].split()[:10]
    assert last_words_chunk0 == first_words_chunk1


# --- 10.2 Embeddings Tests ---

def test_embed_and_persist_roundtrip(in_memory_db, sample_document):
    chunks = ["Circulation loss encountered in Barail sand at 2450m.", "Pumped 40 bbls high-viscosity LCM pill."]
    persisted = embed_chunks(chunks, sample_document.doc_id, db=in_memory_db)
    assert len(persisted) == 2
    assert persisted[0].doc_id == sample_document.doc_id
    assert persisted[0].embedding is not None


def test_embedding_dimension_matches_column(in_memory_db, sample_document):
    provider = MockEmbeddingProvider(dimension=1536)
    chunks = ["Sample text for dimension check."]
    persisted = embed_chunks(chunks, sample_document.doc_id, db=in_memory_db, provider=provider)
    assert len(persisted[0].embedding) == 1536


def test_provider_failure_marks_status_not_silently_skipped(in_memory_db, sample_document):
    failing_provider = MockEmbeddingProvider(should_raise=True)
    with pytest.raises(EmbeddingProviderError):
        embed_chunks(["Test failure"], sample_document.doc_id, db=in_memory_db, provider=failing_provider)

    in_memory_db.refresh(sample_document)
    assert sample_document.extraction_status == "embedding_failed"


# --- 10.3 Search Filters Tests ---

def test_nearby_filter_matches_geospatial_query(in_memory_db, sample_well):
    # Add a nearby well (< 5km) and a far well (> 100km)
    near_well = Well(
        well_id=uuid.uuid4(),
        name="NHK-051",
        status="active",
        latitude=27.36,
        longitude=95.33,
        field_name="Nahorkatiya",
    )
    far_well = Well(
        well_id=uuid.uuid4(),
        name="MKM-001",
        status="active",
        latitude=28.50,
        longitude=96.50,
        field_name="Moran",
    )
    in_memory_db.add_all([near_well, far_well])
    in_memory_db.commit()

    nearby_ids = apply_nearby_filter(sample_well.well_id, radius_km=10.0, db=in_memory_db)
    assert near_well.well_id in nearby_ids
    assert far_well.well_id not in nearby_ids


def test_exclude_superseded_removes_amended_originals(in_memory_db, sample_well):
    doc_original = Document(
        doc_id=uuid.uuid4(),
        well_id=sample_well.well_id,
        doc_type="WCR",
        file_path="uploads/orig.pdf",
    )
    doc_amendment = Document(
        doc_id=uuid.uuid4(),
        well_id=sample_well.well_id,
        doc_type="WCR",
        file_path="uploads/amend.pdf",
        amends_document_id=doc_original.doc_id,
    )
    in_memory_db.add_all([doc_original, doc_amendment])
    in_memory_db.commit()

    active_doc_ids = exclude_superseded([doc_original.doc_id, doc_amendment.doc_id], db=in_memory_db)
    assert doc_original.doc_id not in active_doc_ids
    assert doc_amendment.doc_id in active_doc_ids


def test_exclude_superseded_keeps_non_amended_documents(in_memory_db, sample_well):
    doc1 = Document(doc_id=uuid.uuid4(), well_id=sample_well.well_id, doc_type="WCR", file_path="1.pdf")
    doc2 = Document(doc_id=uuid.uuid4(), well_id=sample_well.well_id, doc_type="DDR", file_path="2.pdf")
    in_memory_db.add_all([doc1, doc2])
    in_memory_db.commit()

    active = exclude_superseded([doc1.doc_id, doc2.doc_id], db=in_memory_db)
    assert doc1.doc_id in active
    assert doc2.doc_id in active


# --- 10.4 Query Parser Tests ---

def test_parses_plain_query():
    parsed = parse_query("What mud weight was used in Barail?")
    assert parsed.text == "What mud weight was used in Barail?"
    assert parsed.well_id is None
    assert parsed.formation is None


def test_resolves_formation_from_active_context(sample_well):
    context_svc = ContextService()
    context_svc.handle_context_update(
        WellContext(
            well_id=sample_well.well_id,
            depth=2450.0,
            formation="Barail",
            timestamp=datetime.now(timezone.utc),
        )
    )
    parsed = parse_query(
        "Any stuck pipe incidents?",
        well_id=sample_well.well_id,
        context_service=context_svc,
    )
    assert parsed.formation == "Barail"


def test_handles_no_well_id_global_search():
    parsed = parse_query("Global lessons learned across all wells", well_id=None)
    assert parsed.well_id is None
    assert parsed.nearby_well_ids is None


# --- 10.5 Keyword Filter Tests ---

def test_keyword_filter_narrows_candidate_set():
    c1 = DocumentChunk(chunk_id=uuid.uuid4(), doc_id=uuid.uuid4(), chunk_text="Loss circulation at 2450m with LCM.")
    c2 = DocumentChunk(chunk_id=uuid.uuid4(), doc_id=uuid.uuid4(), chunk_text="Casing set at 1500m without issues.")
    filtered = apply_keyword_filter([c1, c2], query_text="circulation loss")
    assert len(filtered) == 1
    assert filtered[0].chunk_id == c1.chunk_id


def test_no_match_returns_empty():
    c1 = DocumentChunk(chunk_id=uuid.uuid4(), doc_id=uuid.uuid4(), chunk_text="Routine drilling at 1200m.")
    filtered = apply_keyword_filter([c1], query_text="hydrogen sulfide gas kick")
    assert len(filtered) == 0


# --- 11.1 RAG Citation Validation Gate Tests ---

def test_valid_citations_pass():
    chunk_id = uuid.uuid4()
    raw_res = RawSynthesisResult(
        answer=f"Offset well NHK-001 encountered loss circulation in Barail [source: {chunk_id}].",
        raw_response="...",
    )
    ranked = [RankedChunk(chunk_id=chunk_id, doc_id=uuid.uuid4(), chunk_text="Loss circulation", similarity_score=0.9)]
    resp = validate_citations(raw_res, retrieved_chunk_ids={str(chunk_id)}, chunks=ranked)
    assert resp is not None
    assert str(chunk_id) in resp.answer
    assert len(resp.sources) == 1


def test_hallucinated_citation_id_rejected_and_regenerated():
    valid_id = uuid.uuid4()
    fake_id = uuid.uuid4()
    raw_res = RawSynthesisResult(
        answer=f"Claim grounded in fake source [source: {fake_id}].",
        raw_response="...",
    )
    # fake_id is not in retrieved_chunk_ids -> must be rejected
    resp = validate_citations(raw_res, retrieved_chunk_ids={str(valid_id)})
    assert resp is None


def test_uncited_factual_sentence_rejected():
    chunk_id = uuid.uuid4()
    # First sentence has citation, but second factual sentence has none
    raw_res = RawSynthesisResult(
        answer=f"Barail formation had loss circulation [source: {chunk_id}]. The driller increased mud weight to 12.5 ppg to maintain borehole stability.",
        raw_response="...",
    )
    resp = validate_citations(raw_res, retrieved_chunk_ids={str(chunk_id)})
    assert resp is None


def test_persistent_failure_returns_no_confident_answer_not_a_guess(in_memory_db, sample_document):
    # LLM that always generates uncited text
    bad_llm = MockLLMProvider(canned_text="Uncited claim without source tag.")
    # Add a chunk to DB so search finds candidate
    chunk = DocumentChunk(
        chunk_id=uuid.uuid4(),
        doc_id=sample_document.doc_id,
        chunk_text="Barail sand loss circulation record.",
        embedding=MockEmbeddingProvider().embed_one("Barail sand loss circulation record."),
    )
    in_memory_db.add(chunk)
    in_memory_db.commit()

    parsed = parse_query("loss circulation in Barail")
    resp = run_search(query=parsed, db=in_memory_db, llm=bad_llm)

    assert "No confident, source-grounded answer found" in resp.answer
    assert len(resp.sources) == 0


def test_end_to_end_search_with_fixture_chunks(in_memory_db, sample_well, sample_document):
    chunk_id = uuid.uuid4()
    chunk_text = "Offset well NHK-002 encountered stuck pipe in Barail formation at 2450m."
    chunk = DocumentChunk(
        chunk_id=chunk_id,
        doc_id=sample_document.doc_id,
        chunk_text=chunk_text,
        embedding=MockEmbeddingProvider().embed_one(chunk_text),
    )
    in_memory_db.add(chunk)
    in_memory_db.commit()

    good_llm = MockLLMProvider(
        canned_text=f"Stuck pipe was reported in Barail at 2450m [source: {chunk_id}]."
    )

    parsed = parse_query("stuck pipe in Barail", well_id=sample_well.well_id, db=in_memory_db)
    resp = run_search(query=parsed, db=in_memory_db, llm=good_llm)

    assert resp.answer == f"Stuck pipe was reported in Barail at 2450m [source: {chunk_id}]."
    assert len(resp.sources) == 1
    assert resp.sources[0].chunk_id == str(chunk_id)


def test_no_results_returns_helpful_empty_response(in_memory_db):
    parsed = parse_query("rare unknown formation anomaly")
    resp = run_search(query=parsed, db=in_memory_db)
    assert "No relevant nearby well records" in resp.answer
    assert len(resp.sources) == 0


# --- 11.2 API Router Tests ---

def test_search_api_endpoint(in_memory_db, sample_well, sample_document):
    chunk_id = uuid.uuid4()
    chunk_text = "Offset well NHK-002 encountered stuck pipe in Barail formation at 2450m."
    chunk = DocumentChunk(
        chunk_id=chunk_id,
        doc_id=sample_document.doc_id,
        chunk_text=chunk_text,
        embedding=MockEmbeddingProvider().embed_one(chunk_text),
    )
    in_memory_db.add(chunk)
    in_memory_db.commit()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: in_memory_db

    token = create_token(
        sub=str(uuid.uuid4()),
        email="engineer@oilindia.in",
        name="Chief Engineer",
        roles=["drilling_engineer"],
    )
    headers = {"Authorization": f"Bearer {token}"}

    client = TestClient(app)
    payload = {"query": "stuck pipe Barail", "well_id": str(sample_well.well_id)}

    res = client.post("/search", json=payload, headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "answer" in data
    assert "sources" in data
