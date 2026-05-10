import json
import httpx
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import StreamingResponse
from app.core.config import settings
from app.core.auth import verify_api_key
from app.core.logging import logger
from app.services.transformer.engine import transformer
from app.services.stream.processor import StreamProcessor
from app.utils.error_handler import handle_upstream_error

router = APIRouter()

@router.post("/messages")
async def messages_endpoint(
    request: Request,
    _: str = Depends(verify_api_key)
):
    body = await request.json()
    is_stream = body.get("stream", False)
    model_name = body.get("model", "default")
    
    # 策略控制：默认走 OpenAI 路径（更稳），除非显式配置或检测到特定场景
    use_openai_path = settings.prefer_openai_path 
    
    headers = {
        "Authorization": f"Bearer {settings.litellm_api_key}",
        "Content-Type": "application/json"
    }

    client = httpx.AsyncClient(timeout=600.0)

    try:
        if use_openai_path:
            # --- OpenAI 兼容路径 ---
            openai_request = transformer.transform_request_to_openai(body)
            
            if not is_stream:
                response = await client.post(
                    f"{settings.litellm_url}/v1/chat/completions",
                    json=openai_request,
                    headers=headers
                )
                response.raise_for_status()
                data = response.json()
                return transformer.transform_openai_response_to_anthropic(data)
            else:
                async def stream_generator():
                    processor = StreamProcessor(model_name=model_name)
                    try:
                        async with client.stream(
                            "POST",
                            f"{settings.litellm_url}/v1/chat/completions",
                            json=openai_request,
                            headers=headers
                        ) as response:
                            response.raise_for_status()
                            async for chunk in processor.process_openai_stream(response.aiter_lines()):
                                yield chunk
                    except Exception as e:
                        logger.error(f"Stream error: {str(e)}")
                        try:
                            mapped_exc = handle_upstream_error(e)
                            error_body = mapped_exc.detail
                        except:
                            error_body = {"error": {"type": "api_error", "message": str(e)}}
                        yield f"data: {json.dumps({'type': 'error', **error_body})}\n\n"
                    finally:
                        await client.aclose()
                return StreamingResponse(stream_generator(), media_type="text/event-stream")

        else:
            # --- 原生 Anthropic 路径 (作为备选) ---
            if not is_stream:
                response = await client.post(
                    f"{settings.litellm_url}/v1/messages",
                    json=body,
                    headers=headers
                )
                response.raise_for_status()
                data = response.json()
                return transformer.transform_response(data)
            else:
                async def stream_generator():
                    processor = StreamProcessor(model_name=model_name)
                    try:
                        async with client.stream(
                            "POST",
                            f"{settings.litellm_url}/v1/messages",
                            json=body,
                            headers=headers
                        ) as response:
                            response.raise_for_status()
                            async for chunk in processor.process_anthropic_stream(response.aiter_lines()):
                                yield chunk
                    except Exception as e:
                        logger.error(f"Stream error: {str(e)}")
                        raise handle_upstream_error(e)
                    finally:
                        await client.aclose()
                return StreamingResponse(stream_generator(), media_type="text/event-stream")

    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        raise handle_upstream_error(e)
    except Exception as e:
        logger.error(f"Internal proxy error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/messages/count_tokens")
async def count_tokens_endpoint(
    request: Request,
    _: str = Depends(verify_api_key)
):
    """Token 计数接口逻辑 (同之前)"""
    body = await request.json()
    full_text = ""
    system = body.get("system", "")
    if isinstance(system, str): full_text += system
    elif isinstance(system, list):
        for item in system:
            if item.get("type") == "text": full_text += item.get("text", "")
    for msg in body.get("messages", []):
        content = msg.get("content", "")
        if isinstance(content, str): full_text += content
        elif isinstance(content, list):
            for item in content:
                if item.get("type") == "text": full_text += item.get("text", "")
                elif item.get("type") in ["tool_use", "tool_result"]: full_text += json.dumps(item)
    tools = body.get("tools", [])
    if tools: full_text += json.dumps(tools)
    input_tokens = (len(full_text) // 3) + 20
    return {"input_tokens": input_tokens}
