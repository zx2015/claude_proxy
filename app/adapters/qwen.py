import re
import json
from typing import List, Dict, Any, Tuple
from app.adapters.base import BaseAdapter
from app.adapters.default import DefaultAdapter
from app.core.logging import logger

class QwenAdapter(DefaultAdapter):
    """
    Qwen 专用适配器。
    处理 Qwen 特有的工具调用格式，例如 <call:name>{args}</call> 或类似的变体。
    同时保留对默认 XML 和 MD JSON 的支持。
    """
    def __init__(self):
        super().__init__()
        # 匹配 Qwen 可能出现的 <call:name>arguments</call> 格式
        self.qwen_call_pattern = re.compile(r'<call:([\w\-_]+)>(.*?)</call>', re.DOTALL)
        # 匹配另一种可能的格式 <tool_call>{"name": "...", "arguments": "..."}</tool_call>
        self.qwen_tool_pattern = re.compile(r'<tool_call>(.*?)</tool_call>', re.DOTALL)
        # 匹配 ReAct 格式: Action: name \n Action Input: {args}
        self.react_pattern = re.compile(r'Action:\s*([\w\-_]+).*?Action Input:\s*(.*?)(?:\n\n|Observation:|$)', re.DOTALL)

    def detect_tool_calls(self, text: str) -> Tuple[List[Dict[str, Any]], str]:
        tools = []
        cleaned_text = text

        # 1. 尝试匹配 ReAct 格式 (优先级较高，因为它是文本风格)
        for match in self.react_pattern.finditer(text):
            try:
                name = match.group(1).strip()
                raw_args = match.group(2).strip()
                try:
                    # 尝试解析 JSON，如果不是 JSON 则视为纯文本
                    args = json.loads(raw_args)
                except:
                    args = raw_args

                tools.append(self.build_tool_use_block({
                    "name": name,
                    "input": args
                }))
                cleaned_text = cleaned_text.replace(match.group(0), "")
            except Exception as e:
                logger.warning(f"QwenAdapter: Failed to parse ReAct style: {str(e)}")

        # 2. 尝试匹配 Qwen <call:name> 格式
        for match in self.qwen_call_pattern.finditer(text):
            try:
                name = match.group(1)
                raw_args = match.group(2).strip()
                try:
                    args = json.loads(raw_args)
                except:
                    args = raw_args

                tools.append(self.build_tool_use_block({
                    "name": name,
                    "input": args
                }))
                cleaned_text = cleaned_text.replace(match.group(0), "")
            except Exception as e:
                logger.warning(f"QwenAdapter: Failed to parse <call>: {str(e)}")

        # 2. 尝试匹配 Qwen <tool_call> 格式
        for match in self.qwen_tool_pattern.finditer(cleaned_text):
            try:
                raw_json = match.group(1).strip()
                tool_data = json.loads(raw_json)
                tools.append(self.build_tool_use_block(tool_data))
                cleaned_text = cleaned_text.replace(match.group(0), "")
            except Exception as e:
                logger.warning(f"QwenAdapter: Failed to parse <tool_call>: {str(e)}")

        # 3. 调用父类逻辑检测通用格式 (XML/MD JSON)
        parent_tools, cleaned_text = super().detect_tool_calls(cleaned_text)
        tools.extend(parent_tools)

        return tools, cleaned_text
