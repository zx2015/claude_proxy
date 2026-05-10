import re
import json
from typing import List, Dict, Any, Tuple
from app.adapters.default import DefaultAdapter
from app.core.logging import logger

class QwenAdapter(DefaultAdapter):
    """
    Qwen (通义千问) 专用适配器。
    """
    def inject_system_prompt(self, system_prompt: str) -> str:
        """
        针对 Qwen 增强系统提示词，强制其使用 XML 格式进行工具调用。
        """
        injection = (
            "\n\n[System Instruction]\n"
            "You are an expert at tool calling. When you need to use a tool, "
            "you MUST output your request in the following XML format:\n"
            "<tool_code>\n"
            "{\"name\": \"tool_name\", \"arguments\": {\"arg1\": \"value1\"}}\n"
            "</tool_code>\n"
            "Do not provide any preamble or thoughts inside the XML tag."
        )
        return system_prompt + injection
