import asyncio
import json

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from app.core.auth import verify_api_key
from app.core.config import settings
from app.core.logging import logger
from app.core.request_context import get_request_diagnostics
from app.core.stream_diagnostics import FallbackCorrelator, StreamDiagnostics, fallback_correlator
from app.services.stream.processor import StreamProcessor
from app.services.transformer.engine import transformer
from app.utils.error_handler import handle_upstream_error

router = APIRouter()


def _get_http_client(request: Request):
    shared_client = getattr(request.app.state, "http_client", None)
    if shared_client is not None:
        return shared_client, False
    return httpx.AsyncClient(timeout=600.0), True


def _observe_fallback(request: Request, body: dict, model_name: str, is_stream: bool) -> None:
    if not settings.stream_diagnostics_enabled:
        return
    request_context = get_request_diagnostics(request.scope.get("state", {}))
    if request_context is None:
        return
    model_label = model_name if isinstance(model_name, str) else type(model_name).__name__
    match = fallback_correlator.observe(
        body=body,
        model=model_label,
        stream=is_stream,
        request_id=request_context.request_id,
    )
    if match is not None:
        related_request_id, delta_ms = match
        request_context.diagnostic_event = {
            "possible_stream_fallback": True,
            "related_request_id": related_request_id,
            "fallback_delta_ms": delta_ms,
            "model": model_name,
            "path": request.url.path,
            "stream_requested": False,
        }


def _new_stream_diagnostics(
    request: Request, model_name: str, protocol_path: str
) -> StreamDiagnostics | None:
    if not settings.stream_diagnostics_enabled:
        return None
    request_context = get_request_diagnostics(request.scope.get("state", {}))
    if request_context is None:
        return None
    diagnostics = StreamDiagnostics(request_context.request_id, request_context.started_at)
    model_label = model_name if isinstance(model_name, str) else type(model_name).__name__
    diagnostics.set_request(request.url.path, model_label, protocol_path)
    request_context.stream_diagnostics = diagnostics
    return diagnostics


async def _upstream_lines(response: httpx.Response, diagnostics: StreamDiagnostics | None):
    if diagnostics is None:
        async for line in response.aiter_lines():
            yield line
        return

    async for line in diagnostics.iter_upstream_lines(response):
        yield line
    diagnostics.mark_upstream_eof()


def _record_stream_error(diagnostics: StreamDiagnostics | None, error: Exception, stage: str) -> None:
    if diagnostics is None:
        return
    if isinstance(error, httpx.HTTPStatusError):
        termination = "upstream_http_error"
    elif isinstance(error, httpx.RequestError):
        termination = "upstream_transport_error"
    else:
        termination = "processor_error"
    diagnostics.mark_error(termination, stage, error)


def _stream_error_event(error: Exception) -> str:
    """Serialize a safe Anthropic SSE error event after stream headers were sent."""
    try:
        mapped_exc = handle_upstream_error(error)
        detail = mapped_exc.detail
        mapped_error = detail.get("error") if isinstance(detail, dict) else None
        error_type = (
            mapped_error.get("type")
            if isinstance(mapped_error, dict) and isinstance(mapped_error.get("type"), str)
            else "api_error"
        )
    except Exception:
        error_type = "api_error"

    if isinstance(error, httpx.TimeoutException):
        message = "Upstream request timed out."
    elif isinstance(error, httpx.HTTPStatusError):
        message = "Upstream returned an HTTP error."
    elif isinstance(error, httpx.RequestError):
        message = "Upstream transport error."
    else:
        message = "Stream processing failed."

    payload = {
        "type": "error",
        "error": {"type": error_type, "message": message},
    }
    return f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _observed_output(chunks, diagnostics: StreamDiagnostics | None):
    async for chunk in chunks:
        if diagnostics is not None:
            diagnostics.observe_proxy_chunk(chunk)
        yield chunk


@router.post("/messages")
async def messages_endpoint(
    request: Request,
    _: str = Depends(verify_api_key),
):
    body = await request.json()
    is_stream = bool(body.get("stream", False))
    model_name = body.get("model", "default")
    _observe_fallback(request, body, model_name, is_stream)

    # 策略控制：默认走 OpenAI 路径（更稳），除非显式配置或检测到特定场景。
    use_openai_path = settings.prefer_openai_path

    # 处理与清理 HTTP headers，不记录头字段的值。
    incoming_headers = request.headers
    target_headers = ("x-anthropic-billing-header", "anthropic-beta")
    removed_headers = [header for header in target_headers if header in incoming_headers]
    for header in removed_headers:
        logger.info("Detected Anthropic-specific header: %s. Stripping for upstream compatibility.", header)

    upstream_headers = {
        "Authorization": f"Bearer {settings.litellm_api_key}",
        "Content-Type": "application/json",
    }
    if removed_headers:
        logger.debug("Successfully cleaned upstream headers: %s", ", ".join(removed_headers))

    diagnostics = None
    if is_stream:
        protocol_path = "openai_to_anthropic" if use_openai_path else "anthropic_passthrough"
        diagnostics = _new_stream_diagnostics(request, model_name, protocol_path)

    try:
        if use_openai_path:
            openai_request = transformer.transform_request_to_openai(body)
            client, owns_client = _get_http_client(request)

            if not is_stream:
                try:
                    response = await client.post(
                        f"{settings.litellm_url}/v1/chat/completions",
                        json=openai_request,
                        headers=upstream_headers,
                    )
                    response.raise_for_status()
                    return transformer.transform_openai_response_to_anthropic(response.json())
                finally:
                    if owns_client:
                        await client.aclose()

            async def stream_generator():
                processor = StreamProcessor(model_name=model_name, diagnostics=diagnostics)
                try:
                    async with client.stream(
                        "POST",
                        f"{settings.litellm_url}/v1/chat/completions",
                        json=openai_request,
                        headers=upstream_headers,
                    ) as response:
                        if diagnostics is not None:
                            diagnostics.set_upstream_headers(response.status_code, response.headers)
                        response.raise_for_status()
                        if diagnostics is not None and not (response.headers.get("content-type") or "").lower().startswith("text/event-stream"):
                            diagnostics.mark_protocol_error("UnexpectedContentType")
                        chunks = processor.process_openai_stream(_upstream_lines(response, diagnostics))
                        async for chunk in _observed_output(chunks, diagnostics):
                            yield chunk
                    if diagnostics is not None and diagnostics.termination is None:
                        diagnostics.termination = "normal_eof" if diagnostics.upstream_eof_normal else "unknown_error"
                except asyncio.CancelledError as exc:
                    if diagnostics is not None:
                        diagnostics.mark_client_cancelled()
                    logger.info("Client disconnected or cancelled the stream (OpenAI path).")
                    raise exc
                except Exception as exc:
                    _record_stream_error(diagnostics, exc, "upstream_or_processor")
                    logger.error("Stream error (OpenAI path): %s", type(exc).__name__)
                    error_event = _stream_error_event(exc)
                    if diagnostics is not None:
                        diagnostics.observe_proxy_chunk(error_event)
                    yield error_event
                finally:
                    if owns_client:
                        await client.aclose()

            background = BackgroundTask(client.aclose) if owns_client else None
            return StreamingResponse(
                stream_generator(), media_type="text/event-stream", background=background
            )

        # 原生 Anthropic 路径。
        client, owns_client = _get_http_client(request)
        if not is_stream:
            try:
                response = await client.post(
                    f"{settings.litellm_url}/v1/messages",
                    json=body,
                    headers=upstream_headers,
                )
                response.raise_for_status()
                return transformer.transform_response(response.json())
            finally:
                if owns_client:
                    await client.aclose()

        async def stream_generator():
            processor = StreamProcessor(model_name=model_name, diagnostics=diagnostics)
            try:
                async with client.stream(
                    "POST",
                    f"{settings.litellm_url}/v1/messages",
                    json=body,
                    headers=upstream_headers,
                ) as response:
                    if diagnostics is not None:
                        diagnostics.set_upstream_headers(response.status_code, response.headers)
                    response.raise_for_status()
                    if diagnostics is not None and not (response.headers.get("content-type") or "").lower().startswith("text/event-stream"):
                        diagnostics.mark_protocol_error("UnexpectedContentType")
                    chunks = processor.process_anthropic_stream(_upstream_lines(response, diagnostics))
                    async for chunk in _observed_output(chunks, diagnostics):
                        yield chunk
                if diagnostics is not None and diagnostics.termination is None:
                    diagnostics.termination = "normal_eof" if diagnostics.upstream_eof_normal else "unknown_error"
            except asyncio.CancelledError as exc:
                if diagnostics is not None:
                    diagnostics.mark_client_cancelled()
                logger.info("Client disconnected or cancelled the stream (Anthropic path).")
                raise exc
            except Exception as exc:
                _record_stream_error(diagnostics, exc, "upstream_or_processor")
                logger.error("Stream error (Anthropic path): %s", type(exc).__name__)
                error_event = _stream_error_event(exc)
                if diagnostics is not None:
                    diagnostics.observe_proxy_chunk(error_event)
                yield error_event
            finally:
                if owns_client:
                    await client.aclose()

        background = BackgroundTask(client.aclose) if owns_client else None
        return StreamingResponse(stream_generator(), media_type="text/event-stream", background=background)

    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise handle_upstream_error(exc)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Internal proxy error: %s", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Internal proxy error.")


@router.post("/messages/count_tokens")
async def count_tokens_endpoint(
    request: Request,
    _: str = Depends(verify_api_key),
):
    """Token 计数接口使用启发式估算，不作为精确计费值。"""
    body = await request.json()
    full_text = ""
    system = body.get("system", "")
    if isinstance(system, str):
        full_text += system
    elif isinstance(system, list):
        for item in system:
            if item.get("type") == "text":
                full_text += item.get("text", "")
    for msg in body.get("messages", []):
        content = msg.get("content", "")
        if isinstance(content, str):
            full_text += content
        elif isinstance(content, list):
            for item in content:
                if item.get("type") == "text":
                    full_text += item.get("text", "")
                elif item.get("type") in ["tool_use", "tool_result"]:
                    full_text += json.dumps(item)
    tools = body.get("tools", [])
    if tools:
        full_text += json.dumps(tools)
    input_tokens = (len(full_text) // 3) + 20
    return {"input_tokens": input_tokens}
