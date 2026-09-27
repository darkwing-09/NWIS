import logging
import sys
from contextvars import ContextVar
from typing import Any, Optional
import structlog
from structlog.typing import EventDict, WrappedLogger

from config.settings import get_settings

trace_id_ctx: ContextVar[Optional[str]] = ContextVar("trace_id", default=None)


def set_trace_id(trace_id: Optional[str]) -> None:
    trace_id_ctx.set(trace_id)


def get_trace_id() -> Optional[str]:
    return trace_id_ctx.get()


def add_trace_id(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    tid = trace_id_ctx.get()
    if tid is not None:
        event_dict["trace_id"] = tid
    return event_dict


_configured = False


def setup_logging(env: Optional[str] = None) -> None:
    global _configured
    if _configured:
        return

    settings = get_settings()
    app_env = env or settings.app_env

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        add_trace_id,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if app_env == "production":
        processors = shared_processors + [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]
    else:
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True),
        ]

    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.DEBUG if settings.debug else logging.INFO,
    )
    _configured = True


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    setup_logging()
    return structlog.get_logger(name)
