import re
import json
from typing import List, Dict, Any, Tuple
from app.adapters.base import BaseAdapter
from app.adapters.default import DefaultAdapter
from app.core.logging import logger

class DeepSeekAdapter(DefaultAdapter):
    """
    DeepSeek 专用适配器。
    处理 DeepSeek 特有的 <think> 标签（思考过程）。
    """
    def __init__(self):
        super().__init__()
        # 匹配 <think>...</think> 标签，包括未闭合的情况（流式处理中常见）
        self.think_pattern = re.compile(r'<think>.*?(?:</think>|$)', re.DOTALL)

    def detect_tool_calls(self, text: str) -> Tuple[List[Dict[str, Any]], str]:
        """
        在检测工具调用前，先清洗掉思考过程。
        """
        # 1. 移除思考过程
        cleaned_text = self.think_pattern.sub('', text)

        # 2. 调用父类逻辑检测工具调用（XML/MD JSON）
        return super().detect_tool_calls(cleaned_text)

    def build_tool_use_block(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        DeepSeek 有时会在工具调用中返回一些非标准字段，在此进行微调。
        """
        # 继承基类的构建逻辑
        block = super().build_tool_use_block(data)

        # 确保 input 是字典格式
        if isinstance(block.get("input"), str):
            try:
                block["input"] = json.loads(block["input"])
            except:
                pass

        return block
