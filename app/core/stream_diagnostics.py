"""Content-free metrics for a single proxied SSE response."""

import codecs
import hashlib
import hmac
import json
import secrets
import time
from collections import OrderedDict, deque
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Deque, Dict, Iterable, List, Optional, Tuple

from app.core.config import settings


ANTHROPIC_EVENT_TYPES = {
    "message_start",
    "content_block_start",
    "content_block_delta",
    "content_block_stop",
    "message_delta",
    "message_stop",
    "error",
}
MAX_SSE_FRAME_BYTES = 1024 * 1024
FALLBACK_WINDOW_SECONDS = 30.0
FALLBACK_CACHE_LIMIT = 10_000


class StreamDiagnostics:
    def __init__(self, request_id: str, request_started_at: float):
        self.request_id = request_id
        self.started_at = request_started_at
        self.model: Optional[str] = None
        self.path: Optional[str] = None
        self.protocol_path: Optional[str] = None
        self.stream_requested = True
        self.build_id = settings.app_build_id
        self.related_request_id: Optional[str] = None
        self.possible_stream_fallback = False
        self.fallback_delta_ms: Optional[float] = None

        self.upstream_status: Optional[int] = None
        self.upstream_content_type: Optional[str] = None
        self.litellm_call_id: Optional[str] = None
        self.upstream_model_group: Optional[str] = None
        self.upstream_retries: Optional[str] = None
        self.upstream_fallbacks: Optional[str] = None
        self.upstream_response_headers_ms: Optional[float] = None
        self.upstream_first_byte_ms: Optional[float] = None
        self.upstream_first_complete_sse_ms: Optional[float] = None
        self.upstream_sse_frames = 0
        self.upstream_json_frames = 0
        self.upstream_malformed_frames = 0
        self.upstream_body_bytes = 0
        self.upstream_done_marker = False
        self.upstream_eof_normal = False
        self.upstream_unterminated_frame = False

        self.proxy_sse_frames = 0
        self.proxy_json_frames = 0
        self.proxy_malformed_frames = 0
        self.proxy_event_counts: Dict[str, int] = {}
        self.proxy_first_valid_event_ms: Optional[float] = None
        self.proxy_first_delta_ms: Optional[float] = None
        self.proxy_message_stop_seen = False
        self.processor_errors = 0
        self.processor_error_types: Dict[str, int] = {}
        self.observer_errors = 0

        self.termination: Optional[str] = None
        self.error_stage: Optional[str] = None
        self.error_type: Optional[str] = None
        self.downstream_send_error: Optional[str] = None
        self.asgi_body_bytes_accepted: Optional[int] = None
        self.asgi_body_messages: Optional[int] = None
        self.asgi_first_body_ms: Optional[float] = None
        self.client_disconnect_seen = False
        self.duration_ms: Optional[float] = None

        self._upstream_frame_data: List[str] = []
        self._upstream_frame_bytes = 0
        self._upstream_frame_overflow = False
        self._upstream_line_buffer = ""
        self._proxy_frame_data: List[str] = []
        self._proxy_frame_bytes = 0
        self._proxy_frame_overflow = False
        self._proxy_line_buffer = ""

    def set_request(self, path: str, model: str, protocol_path: str) -> None:
        self.path = path
        self.model = model
        self.protocol_path = protocol_path

    def set_upstream_headers(self, status_code: int, headers: Any) -> None:
        now_ms = self.elapsed_ms()
        self.upstream_status = status_code
        self.upstream_content_type = headers.get("content-type")
        self.litellm_call_id = headers.get("x-litellm-call-id")
        self.upstream_model_group = headers.get("x-litellm-model-group")
        self.upstream_retries = headers.get("x-litellm-attempted-retries")
        self.upstream_fallbacks = headers.get("x-litellm-attempted-fallbacks")
        self.upstream_response_headers_ms = now_ms

    def observe_upstream_bytes(self, chunk: bytes) -> None:
        if chunk and self.upstream_first_byte_ms is None:
            self.upstream_first_byte_ms = self.elapsed_ms()
        self.upstream_body_bytes += len(chunk)

    async def iter_upstream_lines(self, response: Any) -> AsyncGenerator[str, None]:
        """Observe decoded response bytes while yielding equivalent text lines."""
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        async for chunk in response.aiter_bytes():
            self.observe_upstream_bytes(chunk)
            self._upstream_line_buffer += decoder.decode(chunk)
            for line in self._take_lines("upstream", final=False):
                yield line

        self._upstream_line_buffer += decoder.decode(b"", final=True)
        for line in self._take_lines("upstream", final=True):
            yield line
        if self._upstream_line_buffer:
            line = self._upstream_line_buffer
            self._upstream_line_buffer = ""
            self.observe_upstream_line(line)
            yield line

    def _take_lines(self, direction: str, final: bool) -> Iterable[str]:
        buffer_name = f"_{direction}_line_buffer"
        buffer = getattr(self, buffer_name)
        lines: List[str] = []
        while True:
            candidates = [position for position in (buffer.find("\n"), buffer.find("\r")) if position >= 0]
            if not candidates:
                break
            position = min(candidates)
            delimiter = buffer[position]
            if delimiter == "\r" and position == len(buffer) - 1 and not final:
                break
            delimiter_length = 2 if delimiter == "\r" and buffer[position + 1 : position + 2] == "\n" else 1
            line = buffer[:position]
            buffer = buffer[position + delimiter_length :]
            self._observe_line(direction, line)
            lines.append(line)
        setattr(self, buffer_name, buffer)
        return lines

    def _observe_line(self, direction: str, line: str) -> None:
        try:
            if direction == "upstream":
                self.observe_upstream_line(line)
            else:
                self._observe_proxy_line(line)
        except Exception:
            self.observer_errors += 1

    def observe_upstream_line(self, line: str) -> None:
        if line == "":
            self._finish_sse_frame("upstream")
        elif line.startswith("data:"):
            data = line[5:]
            if data.startswith(" "):
                data = data[1:]
            self._append_frame_data("upstream", data)

    def observe_proxy_chunk(self, chunk: str) -> None:
        try:
            self._proxy_line_buffer += chunk
            for line in self._take_lines("proxy", final=False):
                del line
        except Exception:
            self.observer_errors += 1

    def _observe_proxy_line(self, line: str) -> None:
        if line == "":
            self._finish_sse_frame("proxy")
        elif line.startswith("data:"):
            data = line[5:]
            if data.startswith(" "):
                data = data[1:]
            self._append_frame_data("proxy", data)

    def _append_frame_data(self, direction: str, data: str) -> None:
        byte_count = len(data.encode("utf-8", errors="replace"))
        bytes_name = f"_{direction}_frame_bytes"
        overflow_name = f"_{direction}_frame_overflow"
        current_bytes = getattr(self, bytes_name) + byte_count
        setattr(self, bytes_name, current_bytes)
        if current_bytes > MAX_SSE_FRAME_BYTES:
            setattr(self, overflow_name, True)
            return
        getattr(self, f"_{direction}_frame_data").append(data)

    def _finish_sse_frame(self, direction: str) -> None:
        data_name = f"_{direction}_frame_data"
        bytes_name = f"_{direction}_frame_bytes"
        overflow_name = f"_{direction}_frame_overflow"
        data_parts = getattr(self, data_name)
        overflow = getattr(self, overflow_name)
        if not data_parts and not overflow:
            setattr(self, bytes_name, 0)
            return

        frame_count_name = "upstream_sse_frames" if direction == "upstream" else "proxy_sse_frames"
        malformed_name = "upstream_malformed_frames" if direction == "upstream" else "proxy_malformed_frames"
        json_count_name = "upstream_json_frames" if direction == "upstream" else "proxy_json_frames"
        setattr(self, frame_count_name, getattr(self, frame_count_name) + 1)
        if direction == "upstream" and self.upstream_first_complete_sse_ms is None:
            self.upstream_first_complete_sse_ms = self.elapsed_ms()

        if overflow:
            setattr(self, malformed_name, getattr(self, malformed_name) + 1)
        else:
            raw_data = "\n".join(data_parts)
            if direction == "upstream" and raw_data == "[DONE]":
                self.upstream_done_marker = True
            else:
                try:
                    event = json.loads(raw_data)
                    setattr(self, json_count_name, getattr(self, json_count_name) + 1)
                    if direction == "proxy" and isinstance(event, dict):
                        self._record_proxy_event(event)
                except (json.JSONDecodeError, TypeError):
                    setattr(self, malformed_name, getattr(self, malformed_name) + 1)

        data_parts.clear()
        setattr(self, bytes_name, 0)
        setattr(self, overflow_name, False)

    def _record_proxy_event(self, event: Dict[str, Any]) -> None:
        if self.proxy_first_valid_event_ms is None:
            self.proxy_first_valid_event_ms = self.elapsed_ms()
        event_type = event.get("type")
        if not isinstance(event_type, str) or event_type not in ANTHROPIC_EVENT_TYPES:
            event_type = "other"
        self.proxy_event_counts[event_type] = self.proxy_event_counts.get(event_type, 0) + 1
        if event_type == "content_block_delta" and self.proxy_first_delta_ms is None:
            self.proxy_first_delta_ms = self.elapsed_ms()
        if event_type == "message_stop":
            self.proxy_message_stop_seen = True

    def mark_upstream_eof(self) -> None:
        self.upstream_eof_normal = True
        self.upstream_unterminated_frame = bool(self._upstream_frame_data or self._upstream_frame_overflow)

    def mark_error(self, termination: str, stage: str, error: BaseException) -> None:
        self.termination = termination
        self.error_stage = stage
        self.error_type = type(error).__name__

    def mark_protocol_error(self, error_type: str) -> None:
        self.termination = "upstream_protocol_error"
        self.error_stage = "upstream_headers"
        self.error_type = error_type

    def mark_client_cancelled(self) -> None:
        if self.termination is None:
            self.termination = "client_cancelled"
            self.error_stage = "downstream"
            self.error_type = "CancelledError"

    def record_processor_error(self, error: BaseException) -> None:
        name = type(error).__name__
        self.processor_errors += 1
        self.processor_error_types[name] = self.processor_error_types.get(name, 0) + 1

    def finish_request(self, request_state: Any) -> None:
        self.asgi_body_bytes_accepted = request_state.asgi_body_bytes_accepted
        self.asgi_body_messages = request_state.asgi_body_messages
        self.asgi_first_body_ms = request_state.first_body_ms
        self.client_disconnect_seen = request_state.client_disconnect_seen
        self.downstream_send_error = request_state.downstream_send_error
        self.duration_ms = self.elapsed_ms()
        self.response_status = request_state.response_status
        if self.downstream_send_error:
            self.termination = "downstream_send_error"
        elif request_state.client_disconnect_seen:
            self.termination = "client_disconnect"
        elif self.termination is None:
            self.termination = "normal_eof"

    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self.started_at) * 1000, 3)

    def summary(self) -> Dict[str, Any]:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "event": "stream_summary",
            "request_id": self.request_id,
            "related_request_id": self.related_request_id,
            "possible_stream_fallback": self.possible_stream_fallback,
            "fallback_delta_ms": self.fallback_delta_ms,
            "build_id": self.build_id,
            "path": self.path,
            "model": self.model,
            "stream_requested": self.stream_requested,
            "protocol_path": self.protocol_path,
            "upstream_status": self.upstream_status,
            "upstream_content_type": self.upstream_content_type,
            "litellm_call_id": self.litellm_call_id,
            "upstream_model_group": self.upstream_model_group,
            "upstream_retries": self.upstream_retries,
            "upstream_fallbacks": self.upstream_fallbacks,
            "upstream_response_headers_ms": self.upstream_response_headers_ms,
            "upstream_first_byte_ms": self.upstream_first_byte_ms,
            "upstream_first_complete_sse_ms": self.upstream_first_complete_sse_ms,
            "upstream_sse_frames": self.upstream_sse_frames,
            "upstream_json_frames": self.upstream_json_frames,
            "upstream_malformed_frames": self.upstream_malformed_frames,
            "upstream_body_bytes": self.upstream_body_bytes,
            "upstream_done_marker": self.upstream_done_marker,
            "upstream_eof_normal": self.upstream_eof_normal,
            "upstream_unterminated_frame": self.upstream_unterminated_frame,
            "proxy_sse_frames": self.proxy_sse_frames,
            "proxy_json_frames": self.proxy_json_frames,
            "proxy_malformed_frames": self.proxy_malformed_frames,
            "proxy_event_counts": self.proxy_event_counts,
            "proxy_first_valid_event_ms": self.proxy_first_valid_event_ms,
            "proxy_first_delta_ms": self.proxy_first_delta_ms,
            "proxy_message_stop_seen": self.proxy_message_stop_seen,
            "processor_errors": self.processor_errors,
            "processor_error_types": self.processor_error_types,
            "observer_errors": self.observer_errors,
            "response_status": getattr(self, "response_status", None),
            "asgi_body_bytes_accepted": self.asgi_body_bytes_accepted,
            "asgi_body_messages": self.asgi_body_messages,
            "asgi_first_body_ms": self.asgi_first_body_ms,
            "client_disconnect_seen": self.client_disconnect_seen,
            "downstream_send_error": self.downstream_send_error,
            "termination": self.termination,
            "error_stage": self.error_stage,
            "error_type": self.error_type,
            "duration_ms": self.duration_ms,
        }


class FallbackCorrelator:
    """Short-lived, process-local matching for likely stream fallbacks."""

    def __init__(self) -> None:
        self._secret = secrets.token_bytes(32)
        self._entries: OrderedDict[Tuple[str, str], Deque[Tuple[float, str]]] = OrderedDict()
        self._expiry: OrderedDict[str, Tuple[float, Tuple[str, str]]] = OrderedDict()
        self._entry_count = 0

    def observe(self, body: Dict[str, Any], model: str, stream: bool, request_id: str) -> Optional[Tuple[str, float]]:
        try:
            if not isinstance(body, dict):
                return None
            normalized = dict(body)
            normalized.pop("stream", None)
            canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            fingerprint = hmac.new(self._secret, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
        except (TypeError, ValueError, UnicodeError):
            return None

        now = time.monotonic()
        self._prune(now)
        model_label = model if isinstance(model, str) else type(model).__name__
        key = (model_label, fingerprint)
        if stream:
            queue = self._entries.setdefault(key, deque())
            queue.append((now, request_id))
            self._expiry[request_id] = (now, key)
            self._entry_count += 1
            self._enforce_limit()
            return None

        queue = self._entries.get(key)
        if not queue:
            return None
        timestamp, related_request_id = queue.pop()
        self._entry_count -= 1
        self._expiry.pop(related_request_id, None)
        if not queue:
            self._entries.pop(key, None)
        return related_request_id, round((now - timestamp) * 1000, 3)

    def _prune(self, now: float) -> None:
        while self._expiry:
            request_id, (timestamp, key) = next(iter(self._expiry.items()))
            if now - timestamp <= FALLBACK_WINDOW_SECONDS:
                break
            self._expiry.popitem(last=False)
            queue = self._entries.get(key)
            if not queue:
                continue
            if queue and queue[0][1] == request_id:
                queue.popleft()
                self._entry_count -= 1
            else:
                # The request may already have been consumed as a fallback.
                continue
            if not queue:
                self._entries.pop(key, None)

    def _enforce_limit(self) -> None:
        while self._entry_count > FALLBACK_CACHE_LIMIT and self._entries:
            key, queue = next(iter(self._entries.items()))
            _, request_id = queue.popleft()
            self._entry_count -= 1
            self._expiry.pop(request_id, None)
            if not queue:
                self._entries.pop(key, None)


fallback_correlator = FallbackCorrelator()
