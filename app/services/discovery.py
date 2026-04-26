import httpx
from typing import List, Dict, Any
from app.core.config import settings
from app.core.logging import logger

class ModelDiscoveryService:
    def __init__(self):
        self.client = httpx.AsyncClient(base_url=settings.litellm_url)
        self.headers = {"Authorization": f"Bearer {settings.litellm_api_key}"}

    async def get_models(self) -> List[Dict[str, Any]]:
        """
        从 LiteLLM 获取模型列表并转换为符合规范的格式。
        """
        try:
            response = await self.client.get("/v1/models", headers=self.headers)
            response.raise_for_status()
            data = response.json()
            
            # LiteLLM 返回的通常是 OpenAI 格式的模型列表
            # 我们直接返回，因为 Claude Code 能够解析标准的模型对象
            return data.get("data", [])
        except Exception as e:
            logger.error(f"Failed to fetch models from LiteLLM: {str(e)}")
            # 返回一个基本的兜底列表，防止服务不可用
            return [
                {"id": "claude-3-5-sonnet-20241022", "object": "model"}
            ]

    async def close(self):
        await self.client.aclose()

model_discovery = ModelDiscoveryService()
