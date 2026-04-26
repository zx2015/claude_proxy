import httpx
from fastapi import HTTPException, status
from app.models.anthropic import ErrorResponse, ErrorDetail
from app.core.logging import logger

def handle_upstream_error(e: Exception) -> HTTPException:
    """
    将上游异常转换为 Anthropic 规范的错误响应。
    """
    error_type = "api_error"
    message = str(e)
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR

    if isinstance(e, httpx.HTTPStatusError):
        status_code = e.response.status_code
        if status_code == 429:
            error_type = "rate_limit_error"
        elif status_code in (500, 503):
            error_type = "overloaded_error"
        elif status_code in (401, 403):
            error_type = "authentication_error"
        
        try:
            upstream_detail = e.response.json()
            message = upstream_detail.get("error", {}).get("message") or str(e)
        except:
            message = str(e)

    elif isinstance(e, httpx.TimeoutException):
        error_type = "overloaded_error"
        message = "Upstream request timed out."
        status_code = status.HTTP_504_GATEWAY_TIMEOUT

    logger.error(f"Mapping upstream error: {status_code} -> {error_type}: {message}")

    # 构造 Anthropic 格式的 Body
    error_content = ErrorResponse(
        error=ErrorDetail(type=error_type, message=message)
    ).model_dump()

    return HTTPException(
        status_code=status_code,
        detail=error_content
    )
