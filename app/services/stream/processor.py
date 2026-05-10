import json
import uuid
import re
from typing import AsyncGenerator, Optional, Dict, Any, List, Tuple
from app.services.transformer.engine import transformer
from app.adapters.factory import adapter_factory
from app.core.logging import logger

class StreamProcessor:
    def __init__(self, model_name: str = "default"):
        self.model_name = model_name
        self.adapter = adapter_factory.get_adapter(model_name)
        
        # 状态管理
        self.state = "FORWARDING"
        self.text_buffer = "" # 累计所有已下发的文本
        self.holding_buffer = "" # 拦截中的文本
        self.max_hold_size = 2000 # 增加阈值，防止长 JSON 截断
        
        self.tool_use_emitted = False
        self.current_content_block_index = 0
        self._pending_tools = []
        
        # 触发拦截的标志
        self.triggers = ["<tool", "```json", "<think", "<call", "<tool_call", "Action:"]

    async def process_openai_stream(self, stream: AsyncGenerator[str, None]) -> AsyncGenerator[str, None]:
        """
        处理来自 LiteLLM 的 OpenAI 格式 SSE 流，实时清洗文本并转换为 Anthropic 格式。
        """
        message_id = f"msg_{uuid.uuid4().hex[:24]}"
        
        # 1. 发送 message_start
        yield f"data: {json.dumps({'type': 'message_start', 'message': {'id': message_id, 'type': 'message', 'role': 'assistant', 'model': self.model_name, 'content': [], 'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 0, 'output_tokens': 0}}})}\n\n"
        
        # 2. 发送第一个 content_block_start (text 类型)
        yield f"data: {json.dumps({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})}\n\n"

        tool_calls_map = {} # 存储 OpenAI 结构化 tool_calls
        last_usage = {"input_tokens": 0, "output_tokens": 0}
        finish_reason = None

        async for line in stream:
            if not line.startswith("data: ") or line.strip() == "data: [DONE]":
                continue

            try:
                chunk = json.loads(line[6:])
                
                if chunk.get("usage"):
                    u = chunk["usage"]
                    last_usage["input_tokens"] = u.get("prompt_tokens", last_usage["input_tokens"])
                    last_usage["output_tokens"] = u.get("completion_tokens", last_usage["output_tokens"])

                choices = chunk.get("choices", [{}])
                if not choices: continue
                
                if choices[0].get("finish_reason"):
                    finish_reason = choices[0]["finish_reason"]

                delta = choices[0].get("delta", {})

                # --- 核心逻辑：处理文本增量 ---
                if "content" in delta and delta["content"]:
                    text = delta["content"]
                    
                    # TODO: 实现实时拦截逻辑。
                    # 当前策略：允许文本先流出以保证响应速度，在流末尾通过 detect_tool_calls 补发元数据。
                    # 进阶优化方案 (Look-ahead Buffering):
                    # 1. 发现触发词首字符时进入 PENDING 状态。
                    # 2. 暂停转发，直至确认是工具指令（丢弃）还是普通文本（冲刷）。
                    # 3. 为防止卡顿，需设定 MAX_HOLD_TIME 或 MAX_HOLD_SIZE。
                    
                    async for out_chunk in self._handle_text_delta(text):
                        yield f"data: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': out_chunk}})}\n\n"
                    
                    # 及时下发拦截并解析出的工具调用
                    if self._pending_tools:
                        for tool in self._pending_tools:
                            async for tool_event in self._emit_structured_tool(tool):
                                yield f"data: {json.dumps(tool_event)}\n\n"
                        self._pending_tools = []

                # --- 核心逻辑：处理 OpenAI 原生结构化工具调用 ---
                if "tool_calls" in delta and delta["tool_calls"]:
                    for tc_delta in delta["tool_calls"]:
                        idx = tc_delta["index"]
                        if idx not in tool_calls_map:
                            tool_calls_map[idx] = {"id": tc_delta.get("id"), "name": "", "arguments": ""}
                        
                        if tc_delta.get("id"): tool_calls_map[idx]["id"] = tc_delta["id"]
                        if tc_delta.get("function", {}).get("name"): 
                            tool_calls_map[idx]["name"] += tc_delta["function"]["name"]
                        if tc_delta.get("function", {}).get("arguments"):
                            tool_calls_map[idx]["arguments"] += tc_delta["function"]["arguments"]

            except Exception as e:
                logger.error(f"StreamProcessor: Error processing chunk: {e}")

        # --- 流结束处理 ---
        
        if self.holding_buffer:
            tools, remaining = self.adapter.detect_tool_calls(self.holding_buffer)
            if tools:
                for tool in tools:
                    async for tool_event in self._emit_structured_tool(tool):
                        yield f"data: {json.dumps(tool_event)}\n\n"
                if remaining.strip():
                    yield f"data: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': remaining}})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': self.holding_buffer}})}\n\n"

        yield f"data: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"

        for idx, tc in tool_calls_map.items():
            try:
                args = json.loads(tc["arguments"])
            except:
                args = tc["arguments"]
            
            tool_block = {
                "type": "tool_use",
                "id": tc["id"] or f"toolu_{uuid.uuid4().hex[:24]}",
                "name": tc["name"],
                "input": args
            }
            async for tool_event in self._emit_structured_tool(tool_block):
                yield f"data: {json.dumps(tool_event)}\n\n"

        stop_reason = transformer._map_finish_reason(finish_reason)
        if self.tool_use_emitted:
            stop_reason = "tool_use"

        yield f"data: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': stop_reason, 'stop_sequence': None}, 'usage': {'output_tokens': last_usage['output_tokens']}})}\n\n"
        yield f"data: {json.dumps({'type': 'message_stop'})}\n\n"

    async def _handle_text_delta(self, text: str) -> AsyncGenerator[str, None]:
        """
        内部状态机：处理文本片段，决定转发还是拦截。
        """
        if self.state == "FORWARDING":
            has_trigger = False
            trigger_pos = -1
            for trigger in self.triggers:
                pos = text.find(trigger[0])
                if pos != -1:
                    # 二次确认，防止过度拦截
                    potential_trigger = text[pos:pos+len(trigger)]
                    if any(t.startswith(potential_trigger) for t in self.triggers):
                        has_trigger = True
                        trigger_pos = pos
                        break
            
            if has_trigger:
                if trigger_pos > 0:
                    yield text[:trigger_pos]
                self.holding_buffer = text[trigger_pos:]
                self.state = "HOLDING"
            else:
                yield text
                
        elif self.state == "HOLDING":
            self.holding_buffer += text
            tools, remaining = self.adapter.detect_tool_calls(self.holding_buffer)
            
            if tools:
                self._pending_tools.extend(tools)
                self.tool_use_emitted = True
                self.holding_buffer = remaining
                if not any(t[0] in self.holding_buffer for t in self.triggers):
                    self.state = "FORWARDING"
                    if self.holding_buffer:
                        yield self.holding_buffer
                        self.holding_buffer = ""
            
            elif len(self.holding_buffer) > self.max_hold_size:
                yield self.holding_buffer
                self.holding_buffer = ""
                self.state = "FORWARDING"

    async def _emit_structured_tool(self, tool_block: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        self.current_content_block_index += 1
        idx = self.current_content_block_index
        yield {"type": "content_block_start", "index": idx, "content_block": tool_block}
        yield {"type": "content_block_stop", "index": idx}

    async def process_anthropic_stream(self, stream: AsyncGenerator[str, None]) -> AsyncGenerator[str, None]:
        async for line in stream:
            yield f"{line}\n"
