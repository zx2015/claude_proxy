from fastapi import HTTPException, Security, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.security.api_key import APIKeyHeader
from app.core.config import settings
from app.core.logging import logger
from typing import Optional

# 支持两种方式
api_key_header = APIKeyHeader(name="x-api-key", auto_error=False)
bearer_auth = HTTPBearer(auto_error=False)

async def verify_api_key(
    request: Request,
    api_key: Optional[str] = Security(api_key_header),
    auth: Optional[HTTPAuthorizationCredentials] = Security(bearer_auth)
):
    """
    多模式验证 API Key。
    1. 优先检查 x-api-key 头
    2. 其次检查 Authorization: Bearer 头
    """
    token = api_key or (auth.credentials if auth else None)

    if not token:
        logger.warning(f"Missing credentials from {request.client.host}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API Key",
        )
    
    if token != settings.proxy_api_key:
        logger.warning(f"Invalid API Key attempt from {request.client.host}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key",
        )
    
    return token
