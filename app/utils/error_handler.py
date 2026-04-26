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
        elif status_code in (500, 502, 503, 504):
            error_type = "overloaded_error"
        elif status_code in (401, 403):
            error_type = "authentication_error"
        elif status_code == 400:
            error_type = "invalid_request_error"
        elif status_code == 404:
            error_type = "not_found_error"

        try:
            upstream_detail = e.response.json()
            # 尝试从 OpenAI 格式中提取更具体的消息
            if isinstance(upstream_detail, dict):
                error_obj = upstream_detail.get("error", {})
                message = error_obj.get("message") if isinstance(error_obj, dict) else str(upstream_detail)

                # 特殊处理上下文超长的情况 (OpenAI 通常返回 400 且 message 包含 context_length)
                if status_code == 400 and message and "context_length" in message.lower():
                    error_type = "invalid_request_error" # Anthropic 常用这个，或者根据具体场景映射
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
