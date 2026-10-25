import httpx
from typing import List, Dict, Any, Optional
from app.core.config import settings
from app.core.logging import logger

class ModelDiscoveryService:
    def __init__(self):
        self.headers = {"Authorization": f"Bearer {settings.litellm_api_key}"}

    async def get_models(
        self, client: Optional[httpx.AsyncClient] = None
    ) -> List[Dict[str, Any]]:
        """
        从 LiteLLM 获取模型列表并转换为符合规范的格式。

        Prefer the application-scoped client. When called without an app lifespan
        (for example, in a small ASGI harness), use a short-lived client instead
        of retaining a module-level client across event loops or app lifespans.
        """
        try:
            if client is None:
                async with httpx.AsyncClient(timeout=600.0) as temporary_client:
                    return await self._fetch_models(temporary_client)
            return await self._fetch_models(client)
        except Exception as e:
            logger.error(f"Failed to fetch models from LiteLLM: {str(e)}")
            # 返回一个基本的兜底列表，防止服务不可用
            return [
                {"id": "claude-3-5-sonnet-20241022", "object": "model"}
            ]

    async def _fetch_models(self, client: httpx.AsyncClient) -> List[Dict[str, Any]]:
        response = await client.get(
            f"{settings.litellm_url}/v1/models", headers=self.headers
        )
        response.raise_for_status()
        data = response.json()

        # LiteLLM 返回的通常是 OpenAI 格式的模型列表
        # 我们直接返回，因为 Claude Code 能够解析标准的模型对象
        return data.get("data", [])

model_discovery = ModelDiscoveryService()
