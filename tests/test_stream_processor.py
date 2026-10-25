import json

import pytest

from app.services.stream.processor import StreamProcessor


async def _source(lines):
    for line in lines:
        yield line


def _openai_line(delta, finish_reason=None):
    choice = {"delta": delta}
    if finish_reason:
        choice["finish_reason"] = finish_reason
    return "data: " + json.dumps({"choices": [choice]})


async def _collect(processor, lines):
    output = [chunk async for chunk in processor.process_openai_stream(_source(lines))]
    return [json.loads(frame[6:]) for chunk in output for frame in chunk.strip().split("\n\n") if frame.startswith("data: ")]


def _assert_serial_content_blocks(events):
    open_index = None
    for event in events:
        if event["type"] == "content_block_start":
            assert open_index is None
            open_index = event["index"]
        elif event["type"] == "content_block_delta":
            assert open_index == event["index"]
        elif event["type"] == "content_block_stop":
            assert open_index == event["index"]
            open_index = None
    assert open_index is None


@pytest.mark.anyio
async def test_inline_tool_blocks_are_serial_and_text_order_is_preserved():
    processor = StreamProcessor("default")
    events = await _collect(
        processor,
        [
            _openai_line({"content": "before <"}),
            _openai_line(
                {"content": 'x <tool_code>{"name":"bash","arguments":{"cmd":"pwd"}}'
                 "</tool_code> after"}
            ),
            "data: [DONE]",
        ],
    )

    _assert_serial_content_blocks(events)
    block_starts = [event for event in events if event["type"] == "content_block_start"]
    assert [event["content_block"]["type"] for event in block_starts] == ["text", "tool_use", "text"]
    assert [event["index"] for event in block_starts] == [0, 1, 2]

    first_text_stop = next(i for i, event in enumerate(events) if event == {"type": "content_block_stop", "index": 0})
    tool_start = next(i for i, event in enumerate(events) if event["type"] == "content_block_start" and event["content_block"]["type"] == "tool_use")
    tool_stop = next(i for i, event in enumerate(events) if event == {"type": "content_block_stop", "index": 1})
    second_text_start = next(i for i, event in enumerate(events) if event == block_starts[2])
    assert first_text_stop < tool_start < tool_stop < second_text_start

    tool_delta = next(event for event in events if event.get("delta", {}).get("type") == "input_json_delta")
    assert json.loads(tool_delta["delta"]["partial_json"]) == {"cmd": "pwd"}
    assert "before <x " in "".join(
        event["delta"].get("text", "") for event in events if event["type"] == "content_block_delta"
    )
    assert events[-2]["delta"]["stop_reason"] == "tool_use"


@pytest.mark.anyio
async def test_native_tool_call_does_not_emit_empty_text_block():
    processor = StreamProcessor("default")
    events = await _collect(
        processor,
        [
            _openai_line(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "function": {"name": "bash", "arguments": '{"cmd":"ls"}'},
                        }
                    ]
                },
                finish_reason="tool_calls",
            ),
            "data: [DONE]",
        ],
    )

    _assert_serial_content_blocks(events)
    starts = [event for event in events if event["type"] == "content_block_start"]
    assert len(starts) == 1
    assert starts[0]["content_block"]["type"] == "tool_use"
    assert starts[0]["content_block"]["input"] == {}
    input_delta = next(event for event in events if event.get("delta", {}).get("type") == "input_json_delta")
    assert json.loads(input_delta["delta"]["partial_json"]) == {"cmd": "ls"}
    assert events[-2]["delta"]["stop_reason"] == "tool_use"


@pytest.mark.anyio
async def test_nonmatching_short_trigger_prefix_is_released_immediately():
    processor = StreamProcessor("default")
    events = await _collect(
        processor,
        [
            _openai_line({"content": "<"}),
            _openai_line({"content": "typename T> value"}),
            "data: [DONE]",
        ],
    )

    text = "".join(
        event["delta"].get("text", "")
        for event in events
        if event["type"] == "content_block_delta" and event["delta"]["type"] == "text_delta"
    )
    assert text == "<typename T> value"
