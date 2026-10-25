from fastapi import APIRouter, Depends, Request
from app.services.discovery import model_discovery
from app.core.auth import verify_api_key

router = APIRouter()

@router.get("/models")
async def list_models(request: Request, _: str = Depends(verify_api_key)):
    """
    返回可用的模型列表。
    """
    client = getattr(request.app.state, "http_client", None)
    models = await model_discovery.get_models(client=client)
    return {"data": models, "object": "list"}
