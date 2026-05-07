import uvicorn
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from app.core.config import settings
from app.core.auth import verify_api_key
from app.core.logging import setup_logging
from app.api import models, messages
from app.services.discovery import model_discovery

# 1. 初始化日志
setup_logging()

# 2. 屏蔽健康检查的访问日志 (防止刷屏)
class HealthCheckFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "/health" not in record.getMessage()

# 获取 uvicorn 访问日志记录器并应用过滤器
logging.getLogger("uvicorn.access").addFilter(HealthCheckFilter())

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
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

app.include_router(models.router, prefix="/v1", dependencies=[Depends(verify_api_key)])
app.include_router(messages.router, prefix="/v1", dependencies=[Depends(verify_api_key)])

if __name__ == "__main__":
    # 在容器中或生产环境建议显式关闭 reload 以节省 CPU
    # 只有当 LOG_LEVEL 为 DEBUG 且非容器环境时才建议开启
    should_reload = settings.log_level == "DEBUG"
    
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False, # 强制关闭热重载，彻底解决高 CPU 占用问题
        access_log=True
    )
