from fastapi import APIRouter, Depends
from app.services.discovery import model_discovery
from app.core.auth import verify_api_key

router = APIRouter()

@router.get("/models")
async def list_models(_: str = Depends(verify_api_key)):
    """
    返回可用的模型列表。
    """
    models = await model_discovery.get_models()
    return {"data": models, "object": "list"}
