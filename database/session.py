from typing import Generator, Optional
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from config.settings import get_settings

_engine: Optional[Engine] = None
_session_factory: Optional[sessionmaker] = None


def get_engine() -> Engine:
    """Return singleton SQLAlchemy engine configured from settings."""
    global _engine, _session_factory
    if _engine is not None:
        return _engine

    settings = get_settings()
    db_url = settings.database_url
    engine_kwargs = {}
    if "sqlite" in db_url:
        engine_kwargs["connect_args"] = {"check_same_thread": False}
    else:
        engine_kwargs["pool_size"] = settings.database_pool_size
        engine_kwargs["pool_pre_ping"] = True

    _engine = create_engine(db_url, **engine_kwargs)
    _session_factory = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
    return _engine


def get_session_factory() -> sessionmaker:
    """Return singleton sessionmaker."""
    global _session_factory
    if _session_factory is None:
        get_engine()
    return _session_factory  # type: ignore[return-value]


def get_session() -> Generator[Session, None, None]:
    """FastAPI request-scoped dependency yielding a DB session."""
    factory = get_session_factory()
    session: Session = factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


get_db = get_session


def get_worker_session() -> Session:
    """Explicit session for background workers (closed in finally block)."""
    factory = get_session_factory()
    return factory()


def reset_engine() -> None:
    """Reset engine and session factory (useful for testing)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
    get_settings.cache_clear()

