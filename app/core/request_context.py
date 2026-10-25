"""Request correlation and ASGI response observations."""

import asyncio
import contextvars
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

from app.core.logging import logger


@dataclass
class RequestDiagnostics:
    request_id: str
    started_at: float = field(default_factory=time.perf_counter)
    method: str = ""
    path: str = ""
    response_status: Optional[int] = None
    response_body_complete: bool = False
    asgi_body_bytes_accepted: int = 0
    asgi_body_messages: int = 0
    first_body_ms: Optional[float] = None
    client_disconnect_seen: bool = False
    downstream_send_error: Optional[str] = None
    stream_diagnostics: Any = None
    diagnostic_event: Optional[Dict[str, Any]] = None

    def timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


request_diagnostics_var: contextvars.ContextVar[Optional[RequestDiagnostics]] = (
    contextvars.ContextVar("request_diagnostics", default=None)
)


class RequestContextMiddleware:
    """Pure ASGI middleware that adds a proxy-generated request ID."""

    def __init__(self, app: Callable[..., Awaitable[None]]):
        self.app = app

    async def __call__(self, scope: Dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex
        state = scope.setdefault("state", {})
        request_state = RequestDiagnostics(
            request_id=request_id,
            method=scope.get("method", ""),
            path=scope.get("path", ""),
        )
        state["request_diagnostics"] = request_state
        context_token = request_diagnostics_var.set(request_state)

        async def observed_receive() -> Dict[str, Any]:
            message = await receive()
            if message.get("type") == "http.disconnect" and not request_state.response_body_complete:
                request_state.client_disconnect_seen = True
            return message

        async def observed_send(message: Dict[str, Any]) -> None:
            message_type = message.get("type")
            if message_type == "http.response.start":
                request_state.response_status = message.get("status")
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"x-request-id"
                ]
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": headers}

            try:
                await send(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                request_state.downstream_send_error = type(exc).__name__
                raise

            if message_type == "http.response.body":
                body = message.get("body", b"")
                request_state.asgi_body_bytes_accepted += len(body)
                request_state.asgi_body_messages += 1
                if not message.get("more_body", False):
                    request_state.response_body_complete = True
                if body and request_state.first_body_ms is None:
                    request_state.first_body_ms = round(
                        (time.perf_counter() - request_state.started_at) * 1000, 3
                    )

        try:
            await self.app(scope, observed_receive, observed_send)
        except asyncio.CancelledError:
            if request_state.stream_diagnostics is not None:
                request_state.stream_diagnostics.mark_client_cancelled()
            raise
        finally:
            stream_diagnostics = request_state.stream_diagnostics
            if stream_diagnostics is not None:
                if request_state.downstream_send_error:
                    stream_diagnostics.termination = "downstream_send_error"
                    stream_diagnostics.downstream_send_error = request_state.downstream_send_error
                stream_diagnostics.finish_request(request_state)
                logger.info(json.dumps(stream_diagnostics.summary(), ensure_ascii=False))
            elif request_state.diagnostic_event is not None:
                event = {
                    "event": "request_diagnostic",
                    "timestamp": request_state.timestamp(),
                    "request_id": request_id,
                    **request_state.diagnostic_event,
                }
                logger.info(json.dumps(event, ensure_ascii=False))
            request_diagnostics_var.reset(context_token)

def get_request_diagnostics(scope_state: Dict[str, Any]) -> Optional[RequestDiagnostics]:
    return scope_state.get("request_diagnostics")
