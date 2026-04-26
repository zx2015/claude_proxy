import uuid
import json
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Tuple
from app.core.logging import logger

class BaseAdapter(ABC):
    """
    模型适配器抽象基类。
    """
    
    @abstractmethod
    def detect_tool_calls(self, text: str) -> Tuple[List[Dict[str, Any]], str]:
        """
        识别文本中的工具调用并返回清洗后的文本。
        返回: (工具块列表, 清理后的文本)
        """
        pass

    def build_tool_use_block(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        通用的工具块构建逻辑。
        """
        name = data.get("name") or data.get("tool")
        name_from_command = False
        if not name:
            name = data.get("command")
            name_from_command = True
        
        input_data = data.get("input") or data.get("arguments") or data.get("parameters")
        
        if input_data is None:
            reserved_keys = {"name", "tool", "type", "id"}
            if name_from_command:
                reserved_keys.add("command")
            input_data = {k: v for k, v in data.items() if k not in reserved_keys}

        return {
            "type": "tool_use",
            "id": f"toolu_{uuid.uuid4().hex[:24]}",
            "name": str(name),
            "input": input_data
        }
