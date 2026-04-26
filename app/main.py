import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from app.core.config import settings
from app.core.auth import verify_api_key
from app.core.logging import setup_logging
from app.api import models, messages
from app.services.discovery import model_discovery

# 初始化日志
setup_logging()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: 系统启动时的逻辑
    yield
    # Shutdown: 系统关闭时的逻辑，释放资源
    await model_discovery.close()

app = FastAPI(
    title="Claude Proxy",
    description="A protocol adapter between Claude Code and LiteLLM",
    version="0.1.0",
    lifespan=lifespan
)

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

@app.get("/")
async def root():
    return {"message": "Claude Proxy is running. Target: " + settings.litellm_url}

# 挂载受保护的路由 (需校验 PROXY_API_KEY)
app.include_router(models.router, prefix="/v1", dependencies=[Depends(verify_api_key)])
app.include_router(messages.router, prefix="/v1", dependencies=[Depends(verify_api_key)])

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True if settings.log_level == "DEBUG" else False
    )
