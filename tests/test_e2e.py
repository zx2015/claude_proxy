import pytest
import respx
import json
import httpx
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.core.config import settings

@pytest.fixture
def anyio_backend():
    return "asyncio"

@pytest.mark.anyio
async def test_auth_failure():
    """验证认证失败情况"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/v1/models", headers={"x-api-key": "wrong-key"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid API Key"

@pytest.mark.anyio
@respx.mock
async def test_list_models():
    """验证模型列表获取与透传"""
    # 模拟上游 LiteLLM 响应
    respx.get(f"{settings.litellm_url}/v1/models").mock(return_value=httpx.Response(
        200, 
        json={"data": [{"id": "gpt-4o", "object": "model"}], "object": "list"}
    ))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/v1/models", headers={"x-api-key": settings.proxy_api_key})
    
    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "gpt-4o"

@pytest.mark.anyio
@respx.mock
async def test_messages_non_stream_transformation():
    """验证非流式消息的协议重构（XML 提取）"""
    upstream_response = {
        "id": "msg_123",
        "type": "message",
        "role": "assistant",
        "content": [
            {"type": "text", "text": "I will search for you: <tool_code>{\"name\": \"google_search\", \"arguments\": {\"q\": \"fastapi\"}}</tool_code>"}
        ],
        "model": "deepseek-chat",
        "stop_reason": "end_turn"
    }

    respx.post(f"{settings.litellm_url}/v1/messages").mock(return_value=httpx.Response(
        200, json=upstream_response
    ))

    request_body = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": 1024
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/v1/messages", 
            json=request_body, 
            headers={"x-api-key": settings.proxy_api_key}
        )
    
    assert response.status_code == 200
    data = response.json()
    assert data["stop_reason"] == "tool_use"
    assert len(data["content"]) == 2
    assert data["content"][1]["type"] == "tool_use"
    assert data["content"][1]["name"] == "google_search"
    assert data["content"][1]["input"] == {"q": "fastapi"}

@pytest.mark.anyio
@respx.mock
async def test_messages_stream_passthrough():
    """验证流式消息的基础转发"""
    # 模拟 SSE 流
    async def sse_stream():
        yield ("data: " + json.dumps({"type": "message_start", "message": {"id": "1"}}) + "\n\n").encode()
        yield ("data: " + json.dumps({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}) + "\n\n").encode()
        yield ("data: " + json.dumps({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello"}}) + "\n\n").encode()
        yield ("data: " + json.dumps({"type": "message_stop"}) + "\n\n").encode()

    respx.post(f"{settings.litellm_url}/v1/messages").mock(return_value=httpx.Response(
        200, content=sse_stream(), headers={"Content-Type": "text/event-stream"}
    ))

    request_body = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Hi"}],
        "stream": True
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/v1/messages", 
            json=request_body, 
            headers={"x-api-key": settings.proxy_api_key}
        )
    
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    
    lines = [line async for line in response.aiter_lines() if line.strip()]
    assert len(lines) >= 3
    assert "Hello" in lines[2]

@pytest.mark.anyio
@respx.mock
async def test_upstream_429_mapping():
    """验证上游 429 错误被正确映射为 rate_limit_error"""
    respx.post(f"{settings.litellm_url}/v1/messages").mock(return_value=httpx.Response(
        429, json={"error": {"message": "Rate limit exceeded on LiteLLM"}}
    ))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/v1/messages", 
            json={"model": "test", "messages": [{"role": "user", "content": "Hi"}]}, 
            headers={"x-api-key": settings.proxy_api_key}
        )
    
    assert response.status_code == 429
    data = response.json()
    # FastAPI 的 HTTPException detail 默认会包装一层
    assert data["detail"]["error"]["type"] == "rate_limit_error"
    assert "LiteLLM" in data["detail"]["error"]["message"]
