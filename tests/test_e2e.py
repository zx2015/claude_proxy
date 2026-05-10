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
    assert response.json()["detail"] == "Missing API Key" or response.json()["detail"] == "Invalid API Key"

@pytest.mark.anyio
@respx.mock
async def test_list_models():
    """验证模型列表获取与透传"""
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
    """验证非流式消息的协议重构（OpenAI -> Anthropic）"""
    # 模拟上游 OpenAI 响应
    upstream_response = {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "created": 1677652288,
        "model": "deepseek-chat",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "I will search for you: <tool_code>{\"name\": \"google_search\", \"arguments\": {\"q\": \"fastapi\"}}</tool_code>"
            },
            "finish_reason": "stop"
        }],
        "usage": {
            "prompt_tokens": 9,
            "completion_tokens": 12,
            "total_tokens": 21
        }
    }

    # 注意：现在代码请求的是 /v1/chat/completions
    respx.post(f"{settings.litellm_url}/v1/chat/completions").mock(return_value=httpx.Response(
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

@pytest.mark.anyio
@respx.mock
async def test_messages_stream_openai_transformation():
    """验证流式消息从 OpenAI 到 Anthropic 的转换"""
    async def openai_sse_stream():
        # OpenAI 格式的流
        yield b'data: ' + json.dumps({"choices": [{"delta": {"role": "assistant", "content": "Hello"}, "index": 0}]}).encode() + b'\n\n'
        yield b'data: ' + json.dumps({"choices": [{"delta": {"content": " world"}, "index": 0}]}).encode() + b'\n\n'
        yield b'data: [DONE]\n\n'

    respx.post(f"{settings.litellm_url}/v1/chat/completions").mock(return_value=httpx.Response(
        200, content=openai_sse_stream(), headers={"Content-Type": "text/event-stream"}
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
    # 期望包含 message_start, content_block_start, content_block_delta, content_block_stop, message_delta, message_stop
    assert any("message_start" in line for line in lines)
    assert any("Hello" in line for line in lines)

@pytest.mark.anyio
@respx.mock
async def test_upstream_429_mapping():
    """验证上游 429 错误被正确映射"""
    respx.post(f"{settings.litellm_url}/v1/chat/completions").mock(return_value=httpx.Response(
        429, json={"error": {"message": "Rate limit exceeded"}}
    ))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/v1/messages", 
            json={"model": "test", "messages": [{"role": "user", "content": "Hi"}]}, 
            headers={"x-api-key": settings.proxy_api_key}
        )
    
    assert response.status_code == 429
    data = response.json()
    assert data["detail"]["error"]["type"] == "rate_limit_error"

@pytest.mark.anyio
async def test_count_tokens():
    """验证 Token 计数接口"""
    request_body = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "How many tokens is this?"}]
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/v1/messages/count_tokens", 
            json=request_body, 
            headers={"x-api-key": settings.proxy_api_key}
        )
    
    assert response.status_code == 200
    data = response.json()
    assert "input_tokens" in data
    assert data["input_tokens"] > 0

@pytest.mark.anyio
@respx.mock
async def test_anthropic_fallback_path(monkeypatch):
    """验证当关闭 OpenAI 路径偏好时，原生 Anthropic 转发路径依然工作"""
    # 1. 强制修改配置为使用 Anthropic 路径
    monkeypatch.setattr(settings, "prefer_openai_path", False)
    
    upstream_response = {
        "id": "msg_anthropic_123",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Anthropic path response"}],
        "model": "claude-3",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 10}
    }

    # 模拟上游 Anthropic 响应
    respx.post(f"{settings.litellm_url}/v1/messages").mock(return_value=httpx.Response(
        200, json=upstream_response
    ))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/v1/messages", 
            json={"model": "claude-3", "messages": [{"role": "user", "content": "hi"}]}, 
            headers={"x-api-key": settings.proxy_api_key}
        )
    
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "msg_anthropic_123"
    assert data["content"][0]["text"] == "Anthropic path response"

@pytest.mark.anyio
@respx.mock
async def test_anthropic_stream_fallback_path(monkeypatch):
    """验证原生 Anthropic 流式转发路径"""
    monkeypatch.setattr(settings, "prefer_openai_path", False)
    
    async def anthropic_sse_stream():
        yield b'data: {"type": "message_start", "message": {"id": "msg_stream_1"}}\n\n'
        yield b'data: {"type": "message_stop"}\n\n'

    respx.post(f"{settings.litellm_url}/v1/messages").mock(return_value=httpx.Response(
        200, content=anthropic_sse_stream(), headers={"Content-Type": "text/event-stream"}
    ))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/v1/messages", 
            json={"model": "claude-3", "messages": [{"role": "user", "content": "hi"}], "stream": True}, 
            headers={"x-api-key": settings.proxy_api_key}
        )
    
    assert response.status_code == 200
    lines = [line async for line in response.aiter_lines() if line.strip()]
    assert any("msg_stream_1" in line for line in lines)

@pytest.mark.anyio
@respx.mock
async def test_messages_stream_cleaning():
    """验证流式响应中的内联工具指令是否被成功清洗"""
    async def inline_tool_sse_stream():
        # 模拟包含内联工具指令的流
        yield b'data: ' + json.dumps({"choices": [{"delta": {"role": "assistant", "content": "I will now list files: <tool_code>"}, "index": 0}]}).encode() + b'\n\n'
        yield b'data: ' + json.dumps({"choices": [{"delta": {"content": "{\"name\": \"ls\", \"arguments\": {}}</tool_code> Done."}, "index": 0}]}).encode() + b'\n\n'
        yield b'data: [DONE]\n\n'

    respx.post(f"{settings.litellm_url}/v1/chat/completions").mock(return_value=httpx.Response(
        200, content=inline_tool_sse_stream(), headers={"Content-Type": "text/event-stream"}
    ))

    request_body = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "List files"}],
        "stream": True
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/v1/messages", 
            json=request_body, 
            headers={"x-api-key": settings.proxy_api_key}
        )
    
    assert response.status_code == 200
    
    lines = [line async for line in response.aiter_lines() if line.strip()]
    
    full_text = ""
    tool_use_found = False
    for line in lines:
        if line.startswith("data: "):
            data = json.loads(line[6:])
            if data["type"] == "content_block_delta" and data["delta"]["type"] == "text_delta":
                full_text += data["delta"]["text"]
            if data["type"] == "content_block_start" and data["content_block"]["type"] == "tool_use":
                tool_use_found = True
    
    # 验证指令标签被移除
    assert "<tool_code>" not in full_text
    assert "{\"name\": \"ls\"}" not in full_text
    assert "I will now list files:" in full_text
    assert "Done." in full_text
    # 验证结构化工具块被发出
    assert tool_use_found
