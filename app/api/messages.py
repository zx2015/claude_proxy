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
    
    # 全量转换为 OpenAI 请求格式
    openai_request = transformer.transform_request_to_openai(body)
    
    headers = {
        "Authorization": f"Bearer {settings.litellm_api_key}",
        "Content-Type": "application/json"
    }

    client = httpx.AsyncClient(timeout=600.0)

    try:
        if not is_stream:
            # 非流式处理
            response = await client.post(
                f"{settings.litellm_url}/v1/chat/completions",
                json=openai_request,
                headers=headers
            )
            response.raise_for_status()
            data = response.json()
            return transformer.transform_openai_response_to_anthropic(data)
        else:
            # 流式处理
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
                    yield f"data: {json.dumps({'type': 'error', 'error': {'type': 'api_error', 'message': str(e)}})}\n\n"
                finally:
                    await client.aclose()

            return StreamingResponse(
                stream_generator(),
                media_type="text/event-stream"
            )

    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        raise handle_upstream_error(e)
    except Exception as e:
        logger.error(f"Internal proxy error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
