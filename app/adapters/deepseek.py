import re
import json
from typing import List, Dict, Any, Tuple
from app.adapters.default import DefaultAdapter
from app.core.logging import logger

class DeepSeekAdapter(DefaultAdapter):
    """
    DeepSeek 专用适配器。
    """
    def inject_system_prompt(self, system_prompt: str) -> str:
        """
        针对 DeepSeek 增强系统提示词。
        """
        injection = (
            "\n\n[System Instruction]\n"
            "You are an expert developer. When using tools, please follow the protocol strictly. "
            "If you are using the Thinking/Reasoning model, ensure the tool command is outside the <think> tag."
        )
        return system_prompt + injection
