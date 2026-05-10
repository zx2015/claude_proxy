import json
import uuid
import re
from typing import Dict, Any, List, Optional
from app.core.logging import logger
from app.adapters.factory import adapter_factory

class ProtocolTransformer:
    def transform_request_to_openai(self, anthropic_req: Dict[str, Any]) -> Dict[str, Any]:
        """
        将 Anthropic 格式转换为标准的 OpenAI 请求格式。
        集成 System Prompt 自动增强逻辑。
        """
        model_name = anthropic_req.get("model", "default")
        adapter = adapter_factory.get_adapter(model_name)
        
        openai_req = {
            "model": model_name,
            "messages": [],
            "stream": anthropic_req.get("stream", False),
            "max_tokens": anthropic_req.get("max_tokens", 1024),
            "temperature": anthropic_req.get("temperature", 0.7)
        }

        # 1. 处理 System Prompt 并进行注入增强
        system = anthropic_req.get("system", "")
        if isinstance(system, list):
            system_content = "\n".join([i.get("text", "") for i in system if i.get("type") == "text"])
        else:
            system_content = system
            
        # 调用适配器执行注入
        enhanced_system = adapter.inject_system_prompt(system_content)
        if enhanced_system:
            openai_req["messages"].append({"role": "system", "content": enhanced_system})

        # 2. 处理 Messages
        for msg in anthropic_req.get("messages", []):
            role = msg["role"]
            content = msg["content"]
            
            # 兼容 Anthropic 允许的 message 级别 system role
            if role == "system":
                msg_content = content if isinstance(content, str) else "\n".join([i.get("text", "") for i in content if i.get("type") == "text"])
                openai_req["messages"].append({"role": "system", "content": adapter.inject_system_prompt(msg_content)})
                continue

            new_msg = {"role": role}
            if isinstance(content, str):
                new_msg["content"] = content
            else:
                text_parts = []
                tool_calls = []
                for item in content:
                    if item["type"] == "text":
                        text_parts.append(item["text"])
                    elif item["type"] == "tool_use":
                        tool_calls.append({
                            "id": item["id"],
                            "type": "function",
                            "function": {"name": item["name"], "arguments": json.dumps(item["input"])}
                        })
                    elif item["type"] == "tool_result":
                        openai_req["messages"].append({
                            "role": "tool",
                            "tool_call_id": item["tool_use_id"],
                            "content": str(item.get("content", ""))
                        })
                
                if text_parts:
                    new_msg["content"] = "\n".join(text_parts)
                if tool_calls:
                    new_msg["tool_calls"] = tool_calls
            
            if "content" in new_msg or "tool_calls" in new_msg:
                openai_req["messages"].append(new_msg)

        # 3. 处理 Tools
        if anthropic_req.get("tools"):
            openai_req["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.get("name", "unknown"),
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", {"type": "object", "properties": {}})
                    }
                } for t in anthropic_req["tools"]
            ]
        
        return openai_req

    def transform_openai_response_to_anthropic(self, openai_resp: Dict[str, Any]) -> Dict[str, Any]:
        """
        将 OpenAI 的响应完美转回 Anthropic 格式。
        """
        if not openai_resp.get("choices"):
            return openai_resp
            
        choice = openai_resp["choices"][0]
        msg = choice.get("message", {})
        
        anthropic_resp = {
            "id": f"msg_{uuid.uuid4().hex[:24]}",
            "type": "message",
            "role": "assistant",
            "model": openai_resp.get("model"),
            "content": [],
            "stop_reason": self._map_finish_reason(choice.get("finish_reason")),
            "usage": {
                "input_tokens": openai_resp.get("usage", {}).get("prompt_tokens", 0),
                "output_tokens": openai_resp.get("usage", {}).get("completion_tokens", 0)
            }
        }

        if msg.get("content"):
            anthropic_resp["content"].append({"type": "text", "text": msg["content"]})

        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                try:
                    args = json.loads(tc["function"]["arguments"])
                except:
                    args = tc["function"]["arguments"]
                
                anthropic_resp["content"].append({
                    "type": "tool_use",
                    "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:24]}",
                    "name": tc["function"]["name"],
                    "input": args
                })
            anthropic_resp["stop_reason"] = "tool_use"

        if not msg.get("tool_calls") and msg.get("content"):
            adapter = adapter_factory.get_adapter(anthropic_resp["model"])
            tools, cleaned = adapter.detect_tool_calls(msg["content"])
            if tools:
                anthropic_resp["content"] = []
                if cleaned.strip():
                    anthropic_resp["content"].append({"type": "text", "text": cleaned})
                anthropic_resp["content"].extend(tools)
                anthropic_resp["stop_reason"] = "tool_use"

        return anthropic_resp

    def _map_finish_reason(self, openai_reason: Optional[str]) -> str:
        mapping = {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "function_call": "tool_use",
            "length": "max_tokens",
            "content_filter": "content_filter"
        }
        return mapping.get(openai_reason, "end_turn")

    def transform_response(self, data: Dict[str, Any]) -> Dict[str, Any]:
        model = data.get("model", "default")
        adapter = adapter_factory.get_adapter(model)
        if "content" not in data: return data
        new_content = []
        found = False
        for b in data["content"]:
            if b["type"] == "text":
                tools, cleaned = adapter.detect_tool_calls(b["text"])
                if tools:
                    if cleaned.strip(): new_content.append({"type": "text", "text": cleaned})
                    new_content.extend(tools)
                    found = True
                    continue
            new_content.append(b)
        if found:
            data["content"] = new_content
            data["stop_reason"] = "tool_use"
        return data

transformer = ProtocolTransformer()
