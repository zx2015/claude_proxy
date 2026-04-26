import re
import json
from typing import List, Dict, Any, Tuple
from app.adapters.base import BaseAdapter
from app.core.logging import logger

class DefaultAdapter(BaseAdapter):
    """
    默认适配器，支持 XML 标签和 Markdown JSON 代码块。
    """
    def __init__(self):
        self.xml_pattern = re.compile(r'<tool_code>(.*?)</tool_code>', re.DOTALL)
        self.md_json_pattern = re.compile(r'```json\s*(.*?)\s*```', re.DOTALL)

    def detect_tool_calls(self, text: str) -> Tuple[List[Dict[str, Any]], str]:
        tools = []
        cleaned_text = text

        # 1. 尝试匹配 XML 格式
        for match in self.xml_pattern.finditer(text):
            try:
                raw_json = match.group(1).strip()
                tool_data = json.loads(raw_json)
                tools.append(self.build_tool_use_block(tool_data))
                cleaned_text = cleaned_text.replace(match.group(0), "")
            except Exception as e:
                logger.warning(f"DefaultAdapter: Failed to parse XML: {str(e)}")

        # 2. 尝试匹配 Markdown JSON (仅当无 XML 时)
        if not tools:
            for match in self.md_json_pattern.finditer(text):
                try:
                    raw_json = match.group(1).strip()
                    tool_data = json.loads(raw_json)
                    if any(k in tool_data for k in ["name", "tool", "command", "arguments"]):
                        tools.append(self.build_tool_use_block(tool_data))
                        cleaned_text = cleaned_text.replace(match.group(0), "")
                except Exception as e:
                    logger.warning(f"DefaultAdapter: Failed to parse MD JSON: {str(e)}")

        return tools, cleaned_text
