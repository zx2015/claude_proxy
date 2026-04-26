# 整体架构设计 (Architecture)

## 1. 技术栈
- **核心框架**: FastAPI (Python 3.10+)
- **异步客户端**: HTTPX
- **配置管理**: Pydantic Settings

## 2. 系统拓扑
```text
[Claude Code] -> (Anthropic Protocol) -> [claude_proxy] -> (OpenAI/Anthropic) -> [LiteLLM]
```

## 3. 数据生命周期与处理逻辑

### 3.1 请求阶段 (Request Phase)
1. **认证**: 拦截请求，校验 `PROXY_API_KEY`。
2. **路由映射**: 根据请求的模型名称，映射至 LiteLLM 对应的模型标识符。
3. **协议转换**: 若目标模型在 OpenAI 模式下更稳定，则将 Anthropic 的 `tools` 定义转换为 OpenAI 的 `functions/tools` 格式发给 LiteLLM。

### 3.2 响应阶段 - 非流式 (Response Phase - Blocking)
1. **完整获取**: 等待 LiteLLM 返回完整 JSON 响应。
2. **转换引擎介入**: 
    - 提取 `tool_calls` 对象或从文本块中正则匹配指令。
    - 执行文本清洗，移除冗余标签。
    - 重新组装 `content` 数组，将文本和工具调用分离。
3. **元数据修正**: 无论上游返回何种停止原因，只要存在工具调用，强制改写 `stop_reason` 为 `tool_use`。

### 3.3 响应阶段 - 流式 (Response Phase - Streaming)
1. **SSE 转发**: 逐个处理来自 LiteLLM 的数据块。
2. **缓冲区监测**: 将 `delta` 文本累加至内部缓冲区，实时寻找工具调用的起始与结束标志。
3. **动态注入**: 
    - 拦截原始流的结束事件。
    - 若识别到工具调用，在流末尾手动注入 `content_block_start` (tool_use) 和 `content_block_stop` 事件。
    - 修正 `message_delta` 中的 `stop_reason` 状态。

## 4. 核心组件职责
- **AuthMiddleware**: 负责入站 API Key 鉴权。
- **ModelRegistry**: 动态拉取并管理 LiteLLM 的模型列表。
- **ProtocolTransformer**: 执行 Anthropic 与其他协议间的结构转换与文本清洗。
- **StreamProcessor**: 维护流式状态机，处理 SSE 分片。
- **ErrorHandler**: 统一拦截异常（如 429, 500），并将上游错误转换为 Anthropic 标准错误模型。

## 5. 异常处理流程
1. **拦截**: 捕获 `httpx.HTTPStatusError` 和 `httpx.RequestError` 等网络与协议异常。
2. **映射**: 
    - 429 -> `rate_limit_error`
    - 500/503 -> `overloaded_error`
    - 401/403 -> `authentication_error`
    - Timeout -> `overloaded_error` (上游响应超时)
3. **响应**: 返回 4xx/5xx HTTP 状态码，响应体严格遵循 Anthropic `ErrorResponse` 规范。
