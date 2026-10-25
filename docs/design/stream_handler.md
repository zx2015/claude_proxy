# 流式处理器设计 (SSE Stream Handler)

## 1. 核心挑战：实时文本清洗与延迟平衡
在流式响应中，模型可能会在 `text` 内容中夹杂工具调用指令（如 `<tool_code>`）。为了确保 Claude Code CLI 的 UI 纯净且不触发误判，代理必须实时拦截并移除这些文本，同时在流结束前注入结构化的 `tool_use` 块。

## 2. 状态机设计：预判式拦截
流式处理器维护以下三种状态：

- **FORWARDING (正常转发)**:
    - 将接收到的文本增量（`text_delta`）立即发送给客户端。
    - 持续扫描文本，寻找触发字符（如 `<`, ```, `Action:`）。
    - 一旦发现触发字符，进入 **HOLDING** 状态。

- **HOLDING (拦截持有)**:
    - 停止下发文本增量，将其存入 `holding_buffer`。
    - 在每一片新数据到达时，尝试匹配完整的工具调用闭合标签。
    - **匹配成功**: 从 `holding_buffer` 中提取工具指令并暂存，清理该段指令，将剩余文本发送。
    - **匹配失败且超过阈值 (MAX_HOLD_SIZE)**: 判定为误判，冲刷缓冲区。

- **DONE (流结束)**: 注入拦截到的所有结构化块并修正 `stop_reason`。

## 3. 关键事件处理逻辑 (SSE Mechanics)

### 3.1 `content_block_delta`
- **逻辑**: 这是拦截发生的主要环节。通过监测 `delta.text` 并将其累加到 `stream_buffer`，决定当前的发送状态（转发还是持有）。

### 3.2 `message_delta`
- **逻辑**: 当上游流即将结束时，代理必须拦截此事件。如果检测到工具调用，将 `delta.stop_reason` 从 `stop` 强制重写为 `tool_use`。

### 3.3 `content_block_stop`
- **逻辑**: 这是注入新块的最佳时机。代理会在原始文本块停止后，紧接着注入一个新的 `tool_use` 类型的 `content_block_start` 事件。

## 4. 路径适配：OpenAI -> Anthropic
由于系统默认走 OpenAI 路径，处理器会将 OpenAI 的 `choices[0].delta` 动态映射为上述 Anthropic 事件序列，确保客户端 SDK 的状态机保持同步。

## 5. 优化目标
- **0 延迟感知**: 普通对话无拦截延迟。
- **UI 纯净**: 绝不显示原始指令文本。

## 6. Anthropic Content Block 串行生命周期

Anthropic 流式响应中的 content block 按顺序完成：一个 block 必须先输出 `content_block_stop`，之后才能输出下一个 block 的 `content_block_start`。文本与工具调用混合时，关闭当前文本块后再发送工具块；若后续仍有文本，则使用新的递增 index 开启文本块。

工具块通过以下事件序列输出：

1. `content_block_start`，`content_block.type` 为 `tool_use`，`input` 初始为空对象。
2. 一个或多个 `content_block_delta`，`delta.type` 为 `input_json_delta`，`partial_json` 拼接后是完整 JSON。
3. `content_block_stop`。

仅有原生工具调用且没有文本时，不创建空的 text block。触发词扫描要区分完整触发词和位于数据块尾部、仍可能跨块补全的前缀；确认无效的短前缀应立即冲刷，避免普通代码或数学文本被长时间缓冲。

当前 `DefaultAdapter` 实际识别 `<tool_code>` 和以 `json` 标记的 Markdown fenced code block，因此流式扫描只对这两个开头做跨块预判；单个 `<`、不支持的 `<think`/`<call` 等前缀不会被当作可解析工具调用长期持有。

当前 `DefaultAdapter` 实际识别 `<tool_code>` 和 ````json` 两种格式，因此流式扫描只对这两个开头做跨块预判；单个 `<`、不支持的 `<think`/`<call` 等前缀不会被当作可解析工具调用长期持有。

## 7. 流中途错误事件

`StreamingResponse` 一旦发送 HTTP 响应头，后续异常不能再通过 FastAPI 异常处理器改成普通 JSON 错误响应。OpenAI 转换路径和 Anthropic passthrough 路径都应在生成器内将可恢复的上游/处理异常映射为 Anthropic SSE error event：

```text
event: error
data: {"type":"error","error":{"type":"api_error","message":"..."}}
```

错误帧之后结束流，不继续发送正常结束事件。`CancelledError` 仍单独向上传播，不转换为 error event。错误消息使用安全的通用说明，避免把异常对象中的 URL、响应正文或其他内部信息发送给客户端。
