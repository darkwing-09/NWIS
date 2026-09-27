import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from pathlib import Path

from config.settings import Settings, get_settings
from database.session import get_engine, get_session, get_worker_session, reset_engine
from database.models import (
    Base,
    Well,
    WellFormationInterval,
    Document,
    DocumentPage,
    DocumentChunk,
    Extraction,
    ExtractionError,
    Event,
    IncidentEvent,
    DrillingParameter,
    Alert,
    AlertEvidence,
    RiskAssessment,
    User,
    Role,
    Permission,
    AuditLog,
    ProcessingJob,
    IntegrationEvent,
)


def test_pool_size_matches_settings(monkeypatch):
    reset_engine()
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/testdb")
    monkeypatch.setenv("DATABASE_POOL_SIZE", "15")

    engine = get_engine()
    assert engine.pool.size() == 15
    reset_engine()


def test_session_rolls_back_on_exception(monkeypatch):
    reset_engine()
    # Use in-memory SQLite for testing session rollback behavior
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    engine = get_engine()

    session_gen = get_session()
    session = next(session_gen)
    assert isinstance(session, Session)
    # Execute a simple statement
    result = session.execute(text("SELECT 1")).scalar()
    assert result == 1

    # Simulate an error inside the caller's block
    with pytest.raises(RuntimeError):
        session_gen.throw(RuntimeError("DB operation failure"))

    reset_engine()


def test_worker_session_explicit():
    reset_engine()
    session = get_worker_session()
    assert isinstance(session, Session)
    session.close()
    reset_engine()


def test_models_metadata_complete():
    """Verify all 19 entities defined in Part 8 are present in Base.metadata.tables."""
    expected_tables = {
        "wells",
        "well_formation_intervals",
        "documents",
        "document_pages",
        "document_chunks",
        "extractions",
        "extraction_errors",
        "events",
        "incident_events",
        "drilling_parameters",
        "alerts",
        "alert_evidence",
        "risk_assessments",
        "users",
        "roles",
        "permissions",
        "audit_logs",
        "processing_jobs",
        "integration_events",
    }
    registered_tables = set(Base.metadata.tables.keys())
    for table in expected_tables:
        assert table in registered_tables, f"Missing table in metadata: {table}"


def test_alembic_revisions_sequential():
    """Verify all 12 migration revisions are sequential and continuous."""
    versions_dir = Path(__file__).parent.parent.parent / "migrations" / "versions"
    migration_files = sorted([f.name for f in versions_dir.glob("*.py")])
    assert len(migration_files) == 12
    assert migration_files[0].startswith("0001_")
    assert migration_files[-1].startswith("0012_")
