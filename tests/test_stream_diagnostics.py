import json

import pytest

from app.core.stream_diagnostics import FallbackCorrelator, StreamDiagnostics


class ChunkedResponse:
    def __init__(self, chunks):
        self.chunks = chunks

    async def aiter_bytes(self):
        for chunk in self.chunks:
            yield chunk


@pytest.mark.anyio
async def test_upstream_line_observer_handles_split_utf8_and_crlf():
    raw = 'data: {"type":"message_start","label":"你好"}\r\n\r\n'.encode("utf-8")
    split = raw.index("你".encode("utf-8")) + 1
    response = ChunkedResponse([raw[:split], raw[split:-3], raw[-3:]])
    diagnostics = StreamDiagnostics("request-1", 0.0)

    lines = [line async for line in diagnostics.iter_upstream_lines(response)]
    diagnostics.mark_upstream_eof()

    assert lines == ['data: {"type":"message_start","label":"你好"}', ""]
    assert diagnostics.upstream_body_bytes == len(raw)
    assert diagnostics.upstream_sse_frames == 1
    assert diagnostics.upstream_json_frames == 1
    assert diagnostics.upstream_malformed_frames == 0
    assert diagnostics.upstream_eof_normal is True


def test_fallback_correlator_matches_only_same_request_shape_and_model():
    correlator = FallbackCorrelator()
    stream_body = {"model": "m1", "stream": True, "messages": [{"role": "user", "content": "x"}]}
    retry_body = {"messages": [{"content": "x", "role": "user"}], "stream": False, "model": "m1"}

    assert correlator.observe(stream_body, "m1", True, "stream-id") is None
    assert correlator.observe(retry_body, "m1", False, "retry-id") is not None

    correlator.observe(stream_body, "m1", True, "other-stream")
    assert correlator.observe(retry_body, "m2", False, "wrong-model") is None
    different_body = {**retry_body, "messages": [{"role": "user", "content": "different"}]}
    assert correlator.observe(different_body, "m1", False, "different-request") is None
