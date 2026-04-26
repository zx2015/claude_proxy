from typing import Dict
from app.adapters.base import BaseAdapter
from app.adapters.default import DefaultAdapter

class AdapterFactory:
    def __init__(self):
        self._default_adapter = DefaultAdapter()
        self._adapters: Dict[str, BaseAdapter] = {}
        # 在此处注册特定模型的适配器，例如：
        # self._adapters["deepseek"] = DeepSeekAdapter()

    def get_adapter(self, model_name: str) -> BaseAdapter:
        """
        根据模型名称获取最匹配的适配器。
        """
        model_name_lower = model_name.lower()
        for key, adapter in self._adapters.items():
            if key in model_name_lower:
                return adapter
        return self._default_adapter

adapter_factory = AdapterFactory()
