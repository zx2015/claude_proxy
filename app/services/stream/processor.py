import json
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from app.adapters.factory import adapter_factory
from app.core.logging import logger
from app.core.stream_diagnostics import StreamDiagnostics
from app.services.transformer.engine import transformer


class StreamProcessor:
    def __init__(self, model_name: str = "default", diagnostics: Optional[StreamDiagnostics] = None):
        self.model_name = model_name
        self.adapter = adapter_factory.get_adapter(model_name)
        self.diagnostics = diagnostics

        self.state = "FORWARDING"
        self.holding_buffer = ""
        self.max_hold_size = 2000
        self.tool_use_emitted = False
        self.current_content_block_index = 0
        self.text_block_index: Optional[int] = None
        self.triggers = ["<tool_code>", "```json"]

    async def process_openai_stream(self, stream: AsyncGenerator[str, None]) -> AsyncGenerator[str, None]:
        """Convert an OpenAI SSE stream into serial Anthropic content blocks."""
        message_id = f"msg_{uuid.uuid4().hex[:24]}"
        yield self._event(
            {
                "type": "message_start",
                "message": {
                    "id": message_id,
                    "type": "message",
                    "role": "assistant",
                    "model": self.model_name,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            }
        )

        tool_calls_map: Dict[int, Dict[str, Optional[str]]] = {}
        last_usage = {"input_tokens": 0, "output_tokens": 0}
        finish_reason = None

        async for line in stream:
            if not line.startswith("data:"):
                continue
            data = line[5:].lstrip()
            if data == "[DONE]":
                continue
            try:
                chunk = json.loads(data)
                if chunk.get("usage"):
                    usage = chunk["usage"]
                    last_usage["input_tokens"] = usage.get("prompt_tokens", last_usage["input_tokens"])
                    last_usage["output_tokens"] = usage.get("completion_tokens", last_usage["output_tokens"])

                choices = chunk.get("choices", [{}])
                if not choices:
                    continue
                choice = choices[0]
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
                delta = choice.get("delta", {})

                if delta.get("content"):
                    for action, value in self._handle_text_delta(delta["content"]):
                        if action == "text":
                            async for event in self._emit_text_delta(value):
                                yield event
                        else:
                            async for event in self._stop_text_block():
                                yield event
                            async for event in self._emit_structured_tool(value):
                                yield event

                for tool_delta in delta.get("tool_calls", []) or []:
                    index = tool_delta["index"]
                    if index not in tool_calls_map:
                        tool_calls_map[index] = {"id": None, "name": "", "arguments": ""}
                    call = tool_calls_map[index]
                    if tool_delta.get("id"):
                        call["id"] = str(call.get("id") or "") + tool_delta["id"]
                    function = tool_delta.get("function", {})
                    if function.get("name"):
                        call["name"] = str(call.get("name") or "") + function["name"]
                    if function.get("arguments"):
                        call["arguments"] = str(call.get("arguments") or "") + function["arguments"]
            except Exception as exc:
                if self.diagnostics is not None:
                    self.diagnostics.record_processor_error(exc)
                logger.error("StreamProcessor chunk parse failure: %s", type(exc).__name__)

        for action, value in self._finish_holding_buffer():
            if action == "text":
                async for event in self._emit_text_delta(value):
                    yield event
            else:
                async for event in self._stop_text_block():
                    yield event
                async for event in self._emit_structured_tool(value):
                    yield event

        async for event in self._stop_text_block():
            yield event

        for _, tool_call in sorted(tool_calls_map.items()):
            try:
                tool_input = json.loads(str(tool_call.get("arguments") or "{}"))
                if not isinstance(tool_input, dict):
                    raise ValueError("Tool input must be a JSON object")
            except (json.JSONDecodeError, ValueError):
                if self.diagnostics is not None:
                    self.diagnostics.record_processor_error(ValueError("Invalid tool arguments"))
                tool_input = {}
            tool_block = {
                "type": "tool_use",
                "id": tool_call.get("id") or f"toolu_{uuid.uuid4().hex[:24]}",
                "name": tool_call.get("name") or "unknown",
                "input": tool_input,
            }
            async for event in self._emit_structured_tool(tool_block):
                yield event

        stop_reason = transformer._map_finish_reason(finish_reason)
        if self.tool_use_emitted or tool_calls_map:
            stop_reason = "tool_use"
        yield self._event(
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": last_usage["output_tokens"]},
            }
        )
        yield self._event({"type": "message_stop"})

    def _find_trigger_candidate(self, text: str) -> Optional[Tuple[int, bool]]:
        """Return the earliest full trigger or suffix that may complete one."""
        for position in range(len(text)):
            suffix = text[position:]
            for trigger in self.triggers:
                if suffix.startswith(trigger):
                    return position, True
                if len(suffix) < len(trigger) and trigger.startswith(suffix):
                    return position, False
        return None

    def _handle_text_delta(self, text: str) -> List[Tuple[str, Any]]:
        if not text:
            return []
        if self.state == "HOLDING":
            self.holding_buffer += text
            return self._process_holding_buffer()

        candidate = self._find_trigger_candidate(text)
        if candidate is None:
            return [("text", text)]

        position, _ = candidate
        actions: List[Tuple[str, Any]] = []
        if position:
            actions.append(("text", text[:position]))
        self.holding_buffer = text[position:]
        self.state = "HOLDING"
        actions.extend(self._process_holding_buffer())
        return actions

    def _process_holding_buffer(self) -> List[Tuple[str, Any]]:
        detect_with_spans = getattr(self.adapter, "detect_tool_calls_with_spans", None)
        remaining = ""
        if detect_with_spans is not None:
            tools, _, spans = detect_with_spans(self.holding_buffer)
        else:
            tools, remaining = self.adapter.detect_tool_calls(self.holding_buffer)
            spans = []

        if tools:
            original = self.holding_buffer
            self.holding_buffer = ""
            self.state = "FORWARDING"
            self.tool_use_emitted = True
            actions: List[Tuple[str, Any]] = []
            if len(spans) != len(tools):
                if remaining:
                    actions.append(("text", remaining))
                actions.extend(("tool", tool) for tool in tools)
                return actions

            cursor = 0
            for tool, (start, end) in zip(tools, spans):
                if start > cursor:
                    actions.extend(self._handle_text_delta(original[cursor:start]))
                actions.append(("tool", tool))
                cursor = end
            if cursor < len(original):
                actions.extend(self._handle_text_delta(original[cursor:]))
            return actions

        candidate = self._find_trigger_candidate(self.holding_buffer)
        if candidate is None:
            plain_text = self.holding_buffer
            self.holding_buffer = ""
            self.state = "FORWARDING"
            return [("text", plain_text)] if plain_text else []

        position, full_trigger = candidate
        if position:
            plain_text = self.holding_buffer[:position]
            self.holding_buffer = self.holding_buffer[position:]
            return [("text", plain_text)]

        if len(self.holding_buffer) > self.max_hold_size:
            plain_text = self.holding_buffer
            self.holding_buffer = ""
            self.state = "FORWARDING"
            return [("text", plain_text)]

        # Keep complete tool prefixes until their closing syntax arrives;
        # retain partial prefixes only while they can still match a trigger.
        if not full_trigger and len(self.holding_buffer) > max(map(len, self.triggers)):
            plain_text = self.holding_buffer
            self.holding_buffer = ""
            self.state = "FORWARDING"
            return [("text", plain_text)]
        return []

    def _finish_holding_buffer(self) -> List[Tuple[str, Any]]:
        if not self.holding_buffer:
            return []
        actions = self._process_holding_buffer()
        if self.holding_buffer:
            plain_text = self.holding_buffer
            self.holding_buffer = ""
            self.state = "FORWARDING"
            actions.append(("text", plain_text))
        return actions

    async def _emit_text_delta(self, text: str) -> AsyncGenerator[str, None]:
        if not text:
            return
        if self.text_block_index is None:
            self.text_block_index = self.current_content_block_index
            self.current_content_block_index += 1
            yield self._event(
                {
                    "type": "content_block_start",
                    "index": self.text_block_index,
                    "content_block": {"type": "text", "text": ""},
                }
            )
        yield self._event(
            {
                "type": "content_block_delta",
                "index": self.text_block_index,
                "delta": {"type": "text_delta", "text": text},
            }
        )

    async def _stop_text_block(self) -> AsyncGenerator[str, None]:
        if self.text_block_index is None:
            return
        yield self._event({"type": "content_block_stop", "index": self.text_block_index})
        self.text_block_index = None

    async def _emit_structured_tool(self, tool_block: Dict[str, Any]) -> AsyncGenerator[str, None]:
        index = self.current_content_block_index
        self.current_content_block_index += 1
        self.tool_use_emitted = True
        tool_start = {**tool_block, "input": {}}
        yield self._event(
            {"type": "content_block_start", "index": index, "content_block": tool_start}
        )
        partial_json = json.dumps(
            tool_block.get("input", {}), ensure_ascii=False, separators=(",", ":")
        )
        yield self._event(
            {
                "type": "content_block_delta",
                "index": index,
                "delta": {"type": "input_json_delta", "partial_json": partial_json},
            }
        )
        yield self._event({"type": "content_block_stop", "index": index})

    @staticmethod
    def _event(event: Dict[str, Any]) -> str:
        return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    async def process_anthropic_stream(self, stream: AsyncGenerator[str, None]) -> AsyncGenerator[str, None]:
        async for line in stream:
            yield f"{line}\n"
