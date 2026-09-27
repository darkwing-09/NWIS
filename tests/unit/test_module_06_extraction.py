from datetime import date
from unittest.mock import MagicMock
import uuid
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config.errors import DocumentNotFoundError, DocumentNotOCRedError, LLMProviderError
from database.models.base import Base
from database.models.documents import Document, Extraction, ExtractionError
from database.models.events import Event, IncidentEvent
from database.models.wells import Well, WellFormationInterval
from domain.ai.llm_provider import MockLLMProvider
from domain.models.enums import EventType
from domain.models.extraction import (
    DepthEventExtraction,
    FormationExtraction,
    IncidentExtraction,
    WellMetadataExtraction,
)
from services.extraction.depth_events import extract_depth_events
from services.extraction.formations import extract_formations
from services.extraction.incidents import extract_incidents, is_narrative_incident_paragraph
from services.extraction.orchestrator import run_extraction
from services.extraction.well_metadata import extract_well_metadata
from services.validation.schemas import (
    DepthEventSchema,
    FormationSchema,
    IncidentSchema,
    WellMetadataSchema,
)


@pytest.fixture
def in_memory_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


# -------------------------------------------------------------
# 06.1 Well Metadata Extractor Tests
# -------------------------------------------------------------

def test_regex_extracts_standard_header(in_memory_db):
    text = """
    OIL INDIA LIMITED
    DRILLING COMPLETION REPORT
    Well Name: NHK-124
    Spud Date: 2023-04-15
    Operator: Oil India Limited
    Field: Nahorkatiya
    """
    doc_id = uuid.uuid4()
    meta = extract_well_metadata(doc_id, text, db=in_memory_db, llm=None)
    assert meta.well_name == "NHK-124"
    assert meta.spud_date == date(2023, 4, 15)
    assert meta.operator == "Oil India Limited"


def test_llm_fallback_on_missing_field(in_memory_db):
    text = """
    DRILLING REPORT
    Field Nahorkatiya. Spudded in spring. Target depth 3400m.
    """
    doc_id = uuid.uuid4()
    mock_llm = MockLLMProvider(
        canned_extractions={
            "WellMetadataSchema": WellMetadataSchema(
                well_name="NHK-999",
                spud_date=date(2022, 5, 20),
                operator="OIL",
                confidence=0.9,
            )
        }
    )
    meta = extract_well_metadata(doc_id, text, db=in_memory_db, llm=mock_llm)
    assert meta.well_name == "NHK-999"
    assert meta.spud_date == date(2022, 5, 20)
    assert meta.operator == "OIL"


def test_fuzzy_match_links_existing_well(in_memory_db):
    well = Well(
        well_id=uuid.uuid4(),
        name="NHK-045",
        status="active",
        latitude=27.3,
        longitude=95.3,
    )
    in_memory_db.add(well)
    in_memory_db.commit()

    text = "Well: NHK-45\nSpud Date: 2021-01-10\nOperator: OIL"
    doc_id = uuid.uuid4()
    meta = extract_well_metadata(doc_id, text, db=in_memory_db, llm=None)
    assert meta.matched_well_id == well.well_id
    assert meta.match_confidence > 0.6


def test_no_match_flags_new_well(in_memory_db):
    text = "Well: COMPLETELY_NEW_WELL_XYZ\nSpud Date: 2021-01-10\nOperator: OIL"
    doc_id = uuid.uuid4()
    meta = extract_well_metadata(doc_id, text, db=in_memory_db, llm=None)
    assert meta.matched_well_id is None
    assert meta.match_confidence == 0.0


# -------------------------------------------------------------
# 06.2 Depth Event Extractor Tests
# -------------------------------------------------------------

def test_regex_prefilter_finds_depth_mentions():
    text = "Drilling progressed normally. At 2450 m encountered severe gas show. Bit pulled at 2500 m."
    mock_llm = MockLLMProvider(
        canned_extractions={
            "DepthEventSchema": DepthEventSchema(
                event_type=EventType.GAS_SHOW,
                depth=2450.0,
                description="Encountered severe gas show",
                confidence=0.95,
            )
        }
    )
    events = extract_depth_events(uuid.uuid4(), text, llm=mock_llm)
    assert len(events) >= 1
    assert events[0].depth == 2450.0
    assert events[0].event_type == "gas_show"


def test_llm_classifies_event_type():
    text = "At 3120 m, lost complete returns with mud loss of 40 bbls."
    mock_llm = MockLLMProvider(
        canned_extractions={
            "DepthEventSchema": DepthEventSchema(
                event_type=EventType.LOSS_CIRCULATION,
                depth=3120.0,
                description="Lost complete returns",
                confidence=0.92,
            )
        }
    )
    events = extract_depth_events(uuid.uuid4(), text, llm=mock_llm)
    assert len(events) == 1
    assert events[0].event_type == "loss_circulation"
    assert not events[0].needs_review


def test_invalid_enum_routed_to_review():
    text = "At 1800 m, experienced anomalous vibrations and strange noise."
    mock_llm = MockLLMProvider(
        canned_extractions={
            "DepthEventSchema": DepthEventSchema(
                event_type=EventType.OTHER,
                depth=1800.0,
                description="strange noise",
                confidence=0.4,
            )
        }
    )
    # Simulate an invalid event type from a provider by mocking the method
    mock_llm.extract_structured = MagicMock(return_value=DepthEventSchema(
        event_type=EventType.OTHER,
        depth=1800.0,
        description="strange noise",
    ))
    # Override schema to have invalid event type string
    mock_invalid = MagicMock()
    mock_invalid.event_type = "completely_invalid_event_type"
    mock_invalid.depth = 1800.0
    mock_invalid.description = "strange noise"
    mock_invalid.confidence = 0.5
    mock_llm.extract_structured = MagicMock(return_value=mock_invalid)

    events = extract_depth_events(uuid.uuid4(), text, llm=mock_llm)
    assert len(events) == 1
    assert events[0].needs_review is True
    assert "Invalid event_type" in events[0].error_reason


def test_no_depth_mentions_returns_empty_list():
    text = "General summary of operation. Crew change completed. Safety meeting held."
    events = extract_depth_events(uuid.uuid4(), text, llm=None)
    assert events == []


# -------------------------------------------------------------
# 06.3 Formation Extractor Tests
# -------------------------------------------------------------

def test_valid_interval_extracted(in_memory_db):
    text = "Barail Formation top at 2100 m, base at 2550 m."
    mock_llm = MockLLMProvider(
        canned_extractions={
            "FormationSchema": FormationSchema(
                formation_name="Barail",
                top_depth=2100.0,
                bottom_depth=2550.0,
                confidence=0.95,
            )
        }
    )
    results = extract_formations(uuid.uuid4(), text, db=in_memory_db, llm=mock_llm)
    assert len(results) == 1
    assert results[0].formation_name == "Barail"
    assert results[0].top_depth == 2100.0
    assert results[0].bottom_depth == 2550.0
    assert results[0].needs_review is False


def test_inverted_depths_rejected(in_memory_db):
    text = "Tipam Formation from 3000 m down to 2500 m."
    # Direct check with schema validation failure
    mock_llm = MockLLMProvider(
        canned_extractions={
            "FormationSchema": {
                "formation_name": "Tipam",
                "top_depth": 3000.0,
                "bottom_depth": 2500.0,
            }
        }
    )
    results = extract_formations(uuid.uuid4(), text, db=in_memory_db, llm=mock_llm)
    assert len(results) == 1
    assert results[0].needs_review is True
    assert "DepthOrderingError" in results[0].error_reason or "greater than" in results[0].error_reason


def test_overlap_with_existing_interval_flagged(in_memory_db):
    well_id = uuid.uuid4()
    well = Well(well_id=well_id, name="NHK-OverlapTest", status="active", latitude=27.0, longitude=95.0)
    in_memory_db.add(well)
    existing = WellFormationInterval(
        interval_id=uuid.uuid4(),
        well_id=well_id,
        formation_name="ExistingFormation",
        top_depth=2000.0,
        bottom_depth=2500.0,
    )
    in_memory_db.add(existing)
    in_memory_db.commit()

    text = "New interval 2200 m to 2600 m."
    mock_llm = MockLLMProvider(
        canned_extractions={
            "FormationSchema": FormationSchema(
                formation_name="NewFormation",
                top_depth=2200.0,
                bottom_depth=2600.0,
                confidence=0.9,
            )
        }
    )
    results = extract_formations(uuid.uuid4(), text, well_id=well_id, db=in_memory_db, llm=mock_llm)
    assert len(results) == 1
    assert results[0].needs_review is True
    assert "OverlapError" in results[0].error_reason


# -------------------------------------------------------------
# 06.4 Incident Extractor Tests
# -------------------------------------------------------------

def test_extracts_narrative_incident():
    text = """
    At 2840 m while drilling ahead with 8-1/2" bit, observed sudden pressure increase of 250 psi and pit gain of 15 bbls.
    Shut in well immediately on annular BOP. Recorded SIDPP 300 psi and SICP 450 psi.
    Pumped 12.5 ppg kill mud and circulated out influx safely through choke manifold.
    """
    mock_llm = MockLLMProvider(
        canned_extractions={
            "IncidentSchema": IncidentSchema(
                cause="Gas influx from porous sand",
                depth=2840.0,
                mitigation="Shut in on annular BOP and pumped kill mud",
                outcome="Influx circulated out safely",
                description=text.strip(),
                confidence=0.94,
            )
        },
        canned_summaries={
            text.strip(): "Gas influx at 2840m circulated out via kill mud"
        }
    )
    incidents = extract_incidents(uuid.uuid4(), text, llm=mock_llm)
    assert len(incidents) == 1
    assert incidents[0].depth == 2840.0
    assert incidents[0].mitigation == "Shut in on annular BOP and pumped kill mud"
    assert incidents[0].auto_title == "Gas influx at 2840m circulated out via kill mud"


def test_generates_title():
    text = """
    While pulling out of hole at 1950 m, pipe became stuck due to differential sticking.
    Spotted pipe lax pill and worked pipe with maximum allowable pull.
    Freed string after 4 hours and resumed POOH.
    """
    mock_llm = MockLLMProvider(
        canned_extractions={
            "IncidentSchema": IncidentSchema(
                cause="Differential sticking",
                depth=1950.0,
                mitigation="Spotted pipe lax pill and worked string",
                outcome="String freed after 4 hours",
                description=text.strip(),
                confidence=0.9,
            )
        },
        canned_summaries={
            text.strip(): "Differential sticking at 1950m freed after pill"
        }
    )
    incidents = extract_incidents(uuid.uuid4(), text, llm=mock_llm)
    assert len(incidents) == 1
    assert incidents[0].auto_title == "Differential sticking at 1950m freed after pill"


def test_ignores_tabular_content():
    tabular = """
    Depth | ROP | WOB | RPM | Flow | SPP | Mud Wt
    1000  | 15  | 20  | 120 | 650  | 2400| 1.15
    1010  | 18  | 22  | 120 | 650  | 2420| 1.15
    1020  | 12  | 20  | 110 | 640  | 2380| 1.15
    """
    assert is_narrative_incident_paragraph(tabular) is False


# -------------------------------------------------------------
# 06.5 Orchestrator & Provenance Tests
# -------------------------------------------------------------

def test_full_extraction_pipeline_happy_path(in_memory_db):
    well = Well(
        well_id=uuid.uuid4(),
        name="NHK-101",
        status="active",
        latitude=27.2,
        longitude=95.2,
    )
    in_memory_db.add(well)
    doc_id = uuid.uuid4()
    doc = Document(
        doc_id=doc_id,
        well_id=well.well_id,
        doc_type="wcr",
        file_path="wells/nhk101/doc.pdf",
        ocr_status="done",
        extraction_status="pending",
        raw_extracted_text="""
        Well Name: NHK-101
        Spud Date: 2023-01-15
        Operator: Oil India Limited

        At 2400 m experienced heavy mud loss into fractured limestone.

        Barail Formation from 2100 m to 2600 m.

        While drilling at 2400 m, severe mud loss occurred. Pumped high-viscosity LCM pill and regained full circulation.
        """,
    )
    in_memory_db.add(doc)
    in_memory_db.commit()

    mock_llm = MockLLMProvider(
        canned_extractions={
            "WellMetadataSchema": WellMetadataSchema(
                well_name="NHK-101",
                spud_date=date(2023, 1, 15),
                operator="Oil India Limited",
            ),
            "DepthEventSchema": DepthEventSchema(
                event_type=EventType.LOSS_CIRCULATION,
                depth=2400.0,
                description="Heavy mud loss into fractured limestone",
            ),
            "FormationSchema": FormationSchema(
                formation_name="Barail",
                top_depth=2100.0,
                bottom_depth=2600.0,
            ),
            "IncidentSchema": IncidentSchema(
                cause="Fractured limestone formation",
                depth=2400.0,
                mitigation="Pumped high-viscosity LCM pill",
                outcome="Regained full circulation",
                description="While drilling at 2400 m, severe mud loss occurred.",
            ),
        }
    )

    result = run_extraction(doc_id, db=in_memory_db, llm=mock_llm)
    assert result.status == "done"
    assert result.events_created > 0
    assert result.needs_review_count == 0

    # Verify document status updated
    refreshed_doc = in_memory_db.get(Document, doc_id)
    assert refreshed_doc.extraction_status == "done"


def test_partial_extractor_failure_isolated(in_memory_db):
    well = Well(well_id=uuid.uuid4(), name="NHK-FailIso", status="active", latitude=27.2, longitude=95.2)
    in_memory_db.add(well)
    doc_id = uuid.uuid4()
    doc = Document(
        doc_id=doc_id,
        well_id=well.well_id,
        doc_type="wcr",
        file_path="wells/nhk_iso/doc.pdf",
        ocr_status="done",
        extraction_status="pending",
        raw_extracted_text="""
        Well: NHK-FailIso
        Spud Date: 2022-01-01
        Operator: OIL
        At 1500 m experienced stuck pipe event.
        """,
    )
    in_memory_db.add(doc)
    in_memory_db.commit()

    # Configure LLM to fail only on DepthEventSchema
    def failing_extract(prompt, schema, text):
        if schema.__name__ == "DepthEventSchema":
            raise LLMProviderError("Depth events model unavailable")
        if schema.__name__ == "WellMetadataSchema":
            return WellMetadataSchema(well_name="NHK-FailIso", spud_date=date(2022, 1, 1), operator="OIL")
        if schema.__name__ == "FormationSchema":
            return FormationSchema(formation_name="Tipam", top_depth=1000.0, bottom_depth=1200.0)
        return IncidentSchema(description="Some narrative incident at 1500 m", depth=1500.0)

    mock_llm = MagicMock()
    mock_llm.extract_structured = MagicMock(side_effect=failing_extract)
    mock_llm.summarize_one_line = MagicMock(return_value="Incident summary")

    result = run_extraction(doc_id, db=in_memory_db, llm=mock_llm)
    # Other extractors succeeded despite depth_events failure
    assert result.well_metadata is not None
    assert result.well_metadata.well_name == "NHK-FailIso"
    assert result.events_created > 0
    # The depth_events failure is recorded in review queue, not halting whole pipeline
    assert result.needs_review_count >= 1


def test_all_extractors_fail_routes_whole_doc_to_review(in_memory_db):
    doc_id = uuid.uuid4()
    doc = Document(
        doc_id=doc_id,
        well_id=None,
        doc_type="wcr",
        file_path="wells/fail_all/doc.pdf",
        ocr_status="done",
        extraction_status="pending",
        raw_extracted_text="Non-parsable unstructured garbage text without wells or formations.",
    )
    in_memory_db.add(doc)
    in_memory_db.commit()

    failing_llm = MockLLMProvider(should_raise=True)
    result = run_extraction(doc_id, db=in_memory_db, llm=failing_llm)

    assert result.status == "needs_review"
    assert result.events_created == 0
    refreshed_doc = in_memory_db.get(Document, doc_id)
    assert refreshed_doc.extraction_status == "needs_review"


def test_provenance_stamped_on_every_persisted_row(in_memory_db):
    well = Well(well_id=uuid.uuid4(), name="NHK-ProvenanceTest", status="active", latitude=27.2, longitude=95.2)
    in_memory_db.add(well)
    doc_id = uuid.uuid4()
    doc = Document(
        doc_id=doc_id,
        well_id=well.well_id,
        doc_type="wcr",
        file_path="wells/nhk_prov/doc.pdf",
        ocr_status="done",
        extraction_status="pending",
        raw_extracted_text="""
        Well: NHK-ProvenanceTest
        Spud Date: 2023-01-01
        Operator: OIL
        At 2750 m experienced severe kick.
        """,
    )
    in_memory_db.add(doc)
    in_memory_db.commit()

    mock_llm = MockLLMProvider(
        canned_extractions={
            "WellMetadataSchema": WellMetadataSchema(well_name="NHK-ProvenanceTest", spud_date=date(2023, 1, 1), operator="OIL"),
            "DepthEventSchema": DepthEventSchema(event_type=EventType.KICK, depth=2750.0, description="Severe kick"),
        }
    )

    result = run_extraction(doc_id, db=in_memory_db, llm=mock_llm)
    assert result.events_created >= 1

    # Check Event table for provenance fields
    events = in_memory_db.query(Event).filter(Event.source_document_id == doc_id).all()
    assert len(events) >= 1
    for ev in events:
        assert ev.extractor_version == "1.0.0"
        assert ev.model_version == "nwis-extractor-v1"
        assert ev.source_document_id == doc_id
        assert ev.valid_from is not None


def test_document_not_ocred_raises_error(in_memory_db):
    doc_id = uuid.uuid4()
    doc = Document(
        doc_id=doc_id,
        doc_type="wcr",
        file_path="wells/test/doc.pdf",
        ocr_status="pending",
        extraction_status="pending",
    )
    in_memory_db.add(doc)
    in_memory_db.commit()

    with pytest.raises(DocumentNotOCRedError):
        run_extraction(doc_id, db=in_memory_db)


def test_document_not_found_raises_error(in_memory_db):
    missing_id = uuid.uuid4()
    with pytest.raises(DocumentNotFoundError):
        run_extraction(missing_id, db=in_memory_db)
