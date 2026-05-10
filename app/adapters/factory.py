from typing import Dict
from app.adapters.base import BaseAdapter
from app.adapters.default import DefaultAdapter
from app.adapters.qwen import QwenAdapter
from app.adapters.deepseek import DeepSeekAdapter

class AdapterFactory:
    def __init__(self):
        self._default_adapter = DefaultAdapter()
        self._adapters: Dict[str, BaseAdapter] = {}
        
        # 注册特定厂商适配器
        self._adapters["qwen"] = QwenAdapter()
        self._adapters["deepseek"] = DeepSeekAdapter()

    def get_adapter(self, model_name: str) -> BaseAdapter:
        """
        根据模型名称获取最匹配的适配器。
        支持对 model_name 进行关键词模糊匹配。
        """
        model_name_lower = model_name.lower()
        for key, adapter in self._adapters.items():
            if key in model_name_lower:
                return adapter
        return self._default_adapter

adapter_factory = AdapterFactory()
