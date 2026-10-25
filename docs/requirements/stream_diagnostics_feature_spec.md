# Feature Spec：流式响应诊断日志

**状态：已实现（2026-10-25）；编写时状态为待 Review**
**版本：1.0**
**范围：Claude Code → `claude_proxy` → LiteLLM 的 `/v1/messages` 流式请求**

## 1. 背景

Claude Code 可能报告：

> Streaming response ended before any complete data was received. Retrying without streaming.

当前代理将 `/v1/messages` 的流式响应转换并通过 `StreamingResponse` 返回，但现有日志主要记录请求状态、通用异常和 HTTPX 的连接过程。访问日志中的 HTTP 200 可能在流生成器尚未完成时记录；上游 `receive_response_body.complete` 仅说明代理读到上游 EOF。两者都不能证明代理解析到了完整 SSE 帧、发出了有效 Anthropic 事件，或客户端收到了这些事件。

本功能增加不含内容的结构化流摘要，以确定流处理链路中最后一个可观测成功阶段，并为流式失败后的非流式重试提供谨慎的关联线索。

## 2. 用户与使用场景

主要用户是维护 `claude_proxy`、LiteLLM 或模型上游的开发和运维人员。用户遇到 Claude Code 流式错误时，应能用请求 ID 和上游 call ID 对照代理与 LiteLLM 日志，并回答：

1. 上游是否返回 SSE，何时到达首个完整数据帧？
2. 代理是否成功解析上游帧并生成 Anthropic 事件？
3. ASGI 是否接受了代理的响应字节？是否观察到客户端断开或代理异常？
4. 是否紧接着发生了形状相同的非流式请求，可能是 Claude Code 降级重试？

## 3. 目标

- 为请求链路提供可搜索的 `request_id`，并记录在响应头 `X-Request-ID`。
- 用一条结构化摘要记录每个流式 `/v1/messages` 请求的关键阶段计数、耗时和终止原因。
- 区分上游空流/异常、SSE 解析问题、代理转换问题、ASGI 写出问题以及观察到的客户端断开。
- 在不记录 prompt、completion、工具参数或原始 SSE 数据的前提下，提供非流式重试候选关联。
- 保留当前 API 响应协议与请求转发行为。

## 4. 非目标

- 不尝试从代理侧证明 Claude Code 已收到或接受数据。ASGI `send()` 成功不是客户端确认。
- 不记录或存储请求体、响应体、SSE data 内容、工具输入、凭据或完整 HTTP 头。
- 不改变流式协议转换、模型路由、重试策略或错误映射。
- 不保证识别所有客户端重试，也不把时间相近的流式与非流式请求认定为同一次逻辑请求。
- 不新增日志 UI、指标服务、数据库表或外部监控依赖。

## 5. 术语

- **完整 SSE 帧**：由空行终止、包含至少一个 `data:` 字段的 SSE 帧。只有可成功解析的 JSON data 帧才计为有效 JSON 帧。
- **Anthropic 事件**：代理输出的 JSON data 对象中有受支持的 `type` 字段的完整 SSE 帧。
- **ASGI 已接受字节**：ASGI `send(http.response.body)` 正常返回对应的响应字节；不代表 TCP 对端确认或应用消费。
- **候选 fallback**：短时间内出现请求形状相同、只有 `stream` 值不同的流式与非流式请求。该标记是诊断线索而非因果结论。

## 6. 功能需求

### 6.1 请求关联

1. 代理为每个入站请求生成不可预测的随机 ID，不直接采用调用方传入的 ID 作为内部主键。
2. 代理将该 ID 放入结构化日志上下文，并在响应头返回 `X-Request-ID`。
3. 流式诊断日志记录 endpoint、模型、`stream` 值和实际协议路径：`openai_to_anthropic` 或 `anthropic_passthrough`。
4. 从上游响应中只记录显式 allowlist 字段：`x-litellm-call-id`、`x-litellm-model-group`、`x-litellm-attempted-retries`、`x-litellm-attempted-fallbacks`。禁止 dump 完整上游响应头。
5. 记录部署版本标识 `build_id`；构建时应将 Git commit 或等价构建标识注入镜像。未配置时值为 `unknown`，不可阻塞服务启动。

### 6.2 上游观测

每条流跟踪并汇总：

- 上游请求开始时间、响应头到达耗时、状态码和 content-type。
- 首个响应字节耗时、首个完整 SSE data 帧耗时。
- 完整 SSE 帧数、有效 JSON 帧数、JSON 解析失败帧数、`[DONE]` 标记是否出现。
- 上游是否正常读到 EOF、异常类别，以及异常发生阶段。
- 上游传输字节总数；该值仅为计数，不包含内容。

若上游返回非 2xx 或非 SSE content-type，应记录为对应状态，不尝试把它归类为空 SSE 流。非 2xx 响应已有的错误处理行为保持不变。

### 6.3 代理转换观测

对代理即将输出的 SSE 帧统计：

- 完整 SSE 帧数、含有效 JSON 的帧数、无效 JSON 帧数。
- 允许列表内的 Anthropic 事件类型计数：`message_start`、`content_block_start`、`content_block_delta`、`content_block_stop`、`message_delta`、`message_stop`、`error`；其他类型只计入 `other`，不输出类型原文。
- 首个完整有效事件、首个 `content_block_delta`、`message_stop` 是否出现以及各自相对请求开始时间。
- StreamProcessor 内部 chunk 解析错误的计数和错误类别。

计数器必须在不改变帧内容、顺序、flush 行为或异常语义的情况下工作。监测逻辑不得消费后丢弃数据，也不得为了日志缓存整条流。

### 6.4 下游/ASGI 观测

- 记录 `http.response.start` 状态码及成功返回的 `http.response.body` 字节数。
- 记录首个响应 body 写出耗时和 ASGI body 消息数。
- 记录是否观察到 `http.disconnect`、协程取消、下游 send 异常及其类别。
- 注明 ASGI send 成功不等于客户端确认收到。不得将 `client_received=true` 作为日志字段。
- Middleware 不得读取或缓存请求体；应保持 ASGI receive/send 生命周期和请求取消语义。

### 6.5 终止分类

每个流式请求最终仅写一个 `stream_summary`，`termination` 使用固定枚举：

- `normal_eof`
- `upstream_http_error`
- `upstream_transport_error`
- `upstream_protocol_error`
- `processor_error`
- `client_cancelled`
- `client_disconnect`
- `downstream_send_error`
- `unknown_error`

尚未观测到的数值字段写 `null`，不能用 `0` 表示缺失。若在响应头发出前失败，记录实际状态码；若响应头已发出、之后流失败，summary 应保留已发出的 HTTP 状态码并记录流终止类别。

### 6.6 非流式重试候选关联

1. 生成仅用于关联的 `request_fingerprint`：对规范化请求 JSON 做 HMAC，计算前移除顶层 `stream` 字段；HMAC 密钥在进程启动时随机生成，不写日志、不持久化。
2. 在进程内仅保存最近 30 秒的流式 fingerprint、模型、`request_id` 和时间戳；不保存请求体或原文。缓存必须有容量上限（建议默认最多 10,000 项），满时淘汰最旧项；只影响候选关联，不影响请求处理。
3. 相同 fingerprint、模型相同、先流式后非流式的请求，给后一个请求增加 `possible_stream_fallback=true`、`related_request_id` 和 `delta_ms`。
4. 关联是 best-effort：进程重启、多 worker、不同实例、请求体变化、缓存淘汰或调度间隔超窗时可能无法匹配；不得因此修改响应、路由或重试。
5. fingerprint 只在运行进程内可关联；日志中仅输出 HMAC 截断值或关联 ID，不输出规范化请求或 HMAC 密钥。

## 7. 日志格式

日志采用一行一个 JSON 对象，使用 UTC ISO-8601 时间。每个 stream 请求结束时写 `stream_summary`。在上游错误、解析错误、客户端断开和 fallback 候选确认时，可额外写简短的阶段事件，但不得逐 token 或逐帧输出日志。

示例（字段值仅用于说明结构）：

```json
{
  "timestamp": "2026-10-25T09:13:14.006Z",
  "event": "stream_summary",
  "request_id": "random-id",
  "related_request_id": null,
  "possible_stream_fallback": false,
  "build_id": "git-sha",
  "path": "/v1/messages",
  "model": "provider/model",
  "stream_requested": true,
  "protocol_path": "openai_to_anthropic",
  "upstream_status": 200,
  "upstream_content_type": "text/event-stream",
  "litellm_call_id": "call-id",
  "upstream_first_byte_ms": 120,
  "upstream_first_complete_sse_ms": 123,
  "upstream_sse_frames": 18,
  "upstream_json_frames": 17,
  "upstream_malformed_frames": 0,
  "upstream_done_marker": true,
  "upstream_eof_normal": true,
  "proxy_event_counts": {
    "message_start": 1,
    "content_block_start": 1,
    "content_block_delta": 16,
    "message_stop": 1
  },
  "proxy_first_valid_event_ms": 124,
  "proxy_first_delta_ms": 130,
  "proxy_message_stop_seen": true,
  "asgi_body_bytes_accepted": 2400,
  "client_disconnect_seen": false,
  "termination": "normal_eof",
  "duration_ms": 900
}
```

## 8. 隐私与安全

- 不记录 prompt、completion、工具参数、完整请求体、原始 SSE data、`Authorization`、API key、cookie 或未经筛选的 headers。
- 不将客户端 IP/端口作为默认日志字段。若排障需要连接关联，使用进程随机密钥生成的短期 `connection_id`；不能反推出原地址。
- 错误日志记录异常类别和安全的阶段枚举，不直接输出可能含 URL、头或响应正文的 `str(exception)`。
- fingerprint 使用进程随机 HMAC 密钥并限制 30 秒存活；不能用于跨进程追踪或身份识别。
- 保留现有日志级别策略；摘要为单请求一条 `INFO` 日志，异常按 `WARNING`/`ERROR` 记录。不得按 token 或每个 SSE 帧输出日志。

## 9. 配置与兼容性

- 新增 `STREAM_DIAGNOSTICS_ENABLED`，默认 `true`；关闭时不执行 fingerprint/诊断计数，不改变现有访问和错误日志。
- Dockerfile 接受可选 build arg `APP_BUILD_ID`，默认 `unknown` 并写入镜像环境；Compose 支持透传。构建时可用 `APP_BUILD_ID=$(git rev-parse --short HEAD) docker compose build` 注入 commit 标识。不要在 Compose runtime `env_file` 中设置 `APP_BUILD_ID`，以免覆盖镜像内值。
- 所有非流式请求仍返回现有 JSON 格式；流式请求仍返回 `text/event-stream`。唯一公开响应变化是增加 `X-Request-ID` 响应头。
- 不新增外部服务、数据库、第三方依赖或必填环境变量。
- 可通过当前 `LOG_LEVEL` 控制应用日志输出；正常情况下每个流只增加一条摘要日志。
- 不在 SSE 帧循环内同步写日志；循环中只更新计数器，摘要在请求结束时写出。
- 诊断代码故障不得中断请求、吞掉取消、阻塞流或改变上游/下游异常传播。若计数器自身出错，应记录诊断失败并继续原始转发。

## 10. 验收标准

实现完成后需满足以下条件：

1. 每个流式 `/v1/messages` 请求有唯一 `request_id`，响应头和所有关联日志一致。
2. 正常 OpenAI 转 Anthropic 流的日志有上游和代理帧计数、首事件耗时、`message_stop` 状态、ASGI 字节数及 `normal_eof`。
3. 原生 Anthropic passthrough 路径可以按相同规则统计帧和终止状态。
4. 上游空流、无效 SSE/JSON、非 SSE content-type、上游连接异常、处理器异常、客户端取消和下游 send 异常都能产生明确且不同的 `termination` 或阶段分类。
5. `http.disconnect` 与 `CancelledError` 不会被误报为正常 EOF；取消语义仍正确传播、上游连接仍关闭。
6. 完全相同但 `stream` 字段不同的请求在同一进程 30 秒窗口内可被标记为 fallback 候选；只要模型或 fingerprint 不同，就不关联。标记仅为日志信息。
7. 对日志做字段级检查，确认没有请求/响应文本、工具参数、凭据、完整 header、原始 SSE data 或客户端明文 IP/端口。
8. 诊断关闭时服务行为与当前实现一致；诊断计数异常时流仍可转发。
9. 日志明确区分“ASGI 接受写出”与“客户端确认收到”，不存在表示客户端已确认的字段。
10. 服务启动日志或每条摘要均能给出 `build_id`；未注入时以 `unknown` 呈现。

## 11. 建议验证场景

- OpenAI 兼容路径：正常多帧流、空流、畸形 JSON 帧、缺少 `[DONE]`、上游中途断开。
- Anthropic passthrough：正常多事件流、缺少 `message_stop`、非 SSE 响应。
- 下游：客户端首帧前断开、输出中途断开、ASGI send 抛错。
- 关联：相同请求 stream=true 后 stream=false、模型不同、请求形状不同、窗口过期、跨 worker/重启。
- 隐私：扫描所有日志事件及异常路径，不包含敏感内容。

## 12. 方案比较与本规格采用的方案

### 方案 A：增加少量阶段日志

记录上游状态、通用异常和开始/结束日志。实现最轻，但无法区分“收到字节”“完整 SSE 帧”和“代理生成 Anthropic 事件”，也难以诊断协议转换问题。

### 方案 B：逐帧详细日志

每个 SSE 帧都记录事件或正文片段。定位细节最多，但日志量大，易暴露用户内容，也会增加流式路径开销。

### 方案 C：无内容计数器加每请求摘要（本规格建议）

在上游解析、代理输出和 ASGI send 三处只统计类型、数量、耗时和状态，结束时写一条摘要；异常时额外写分类日志。可定位丢失阶段，日志量和隐私风险低。fallback 关联使用短时进程内 HMAC fingerprint，结果只作候选提示。

本规格以方案 C 作为 review 提案。最终实现前由用户确认本规格；确认后再编写实施计划并编码。

## 13. 相关文档

- [功能需求文档](functional_requirements.md)
- [流式处理器设计](../design/stream_handler.md)
- [流式响应自动重试诊断记录](../../.learnings/experience/claude-stream-empty-response-diagnosis.md)

## 14. Review 补充范围

以下 review 发现与诊断功能共同影响流式可靠性和排障结果，作为本次已确认实现范围的补充；它们不改变第 3 节所列的诊断目标。

- 同步过期的 transformer 单测，使其调用当前 Adapter API。
- 修复非流式请求的 HTTPX 客户端生命周期，并在应用生命周期内复用连接池。
- 上游错误响应改为 Anthropic 顶层 `type` 和 `error` 结构；认证错误等非上游错误保持原格式。
- 上游 JSON 错误响应体包含与 `X-Request-ID` 响应头相同的 `request_id`，便于关联日志。
- OpenAI 到 Anthropic 的流式 content block 串行闭合；`tool_use` 事件用 `input_json_delta` 发送合法 JSON；无文本时不生成空文本块。
- 拦截触发词只有完整匹配或仍可能跨块完成时才进入缓冲；无效短前缀立即释放。
- 忽略 Python bytecode 和 pytest cache；已追踪的 `.pyc` 从 Git index 移除，保留本地文件。
- `/v1/messages/count_tokens` 继续使用现有启发式估算；文档明确其不是精确 token 计费值，本次不引入 tokenizer 依赖。
- 流式响应头发送后的异常转换为 `event: error` SSE 帧；OpenAI 转换与 Anthropic passthrough 共用错误事件格式，取消仍保持异常传播。
- 模型发现优先复用应用 lifespan 管理的 HTTPX client；无 lifespan 时使用操作级临时 client，不在模块导入阶段创建可跨生命周期复用的 client。

Anthropic 非流式错误响应的外层格式为：

```json
{
  "type": "error",
  "error": {"type": "rate_limit_error", "message": "..."},
  "request_id": "proxy-generated-request-id"
}
```

流式 `tool_use` block 使用 `content_block_start`（`input: {}`）、一个或多个 `content_block_delta`（`input_json_delta.partial_json`）和 `content_block_stop`。前一个 content block 必须先 stop，之后才开始下一个 block。本补充项依据 [Anthropic 错误格式](https://docs.anthropic.com/en/api/errors) 和 [Anthropic Streaming Messages 事件流](https://docs.anthropic.com/en/api/messages-streaming)核对。

> 补充说明：第 3 节“保留当前 API 响应协议”描述最初的诊断功能范围。经用户随后明确授权纳入的 Review 补充范围，特意将**上游错误响应**对齐为 Anthropic 顶层错误外形；这项窄范围协议修正优先于原范围描述。其他本地错误响应仍维持原有外形。
