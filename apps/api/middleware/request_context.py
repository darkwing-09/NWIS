import uuid
from typing import Callable
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from domain.logging.logger import set_trace_id


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Injects and propagates trace_id / request_id across request lifecycle."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        trace_id = (
            request.headers.get("X-Trace-ID")
            or request.headers.get("X-Request-ID")
            or str(uuid.uuid4())
        )
        set_trace_id(trace_id)
        request.state.trace_id = trace_id

        response = await call_next(request)
        response.headers["X-Trace-ID"] = trace_id
        return response
