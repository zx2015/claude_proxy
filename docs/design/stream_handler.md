# 流式处理器设计 (SSE Stream Handler)

## 1. 挑战
在 SSE (Server-Sent Events) 模式下，数据是分片传输的。一个工具调用的 JSON 可能会被拆分成多个 `content_block_delta` 事件。

## 2. 状态机设计
代理需要维护一个临时的流状态：

- **IDLE**：正常转发文本。
- **BUFFERING**：检测到疑似工具调用的开始标志（如 `<tool` 或 ` ```json `），开始将后续 delta 存入缓冲区而不立即下发，或者下发原样文本但保持监测。
- **CONVERTING**：当收到 `message_stop` 或 `content_block_stop` 时，处理缓冲区内的完整字符串。

## 3. 关键事件处理

### 3.1 `content_block_delta`
- 累加 `delta.text` 到 `stream_buffer`。
- 尝试在缓冲区中搜索结束标志。

### 3.2 `message_delta` (含有 `stop_reason`)
- 拦截该事件。
- 如果 `stream_buffer` 中存在已识别的工具调用，将 `delta.stop_reason` 修改为 `"tool_use"`。

### 3.3 `content_block_stop`
- 这是注入新块的最佳时机。
- 如果之前识别到了工具调用，在此事件之前或之后插入一个新的 `tool_use` 类型的块定义事件。

## 4. 实时性折中
为了不增加明显的打字延迟，建议：
- 如果 delta 不包含工具调用特征，立即转发。
- 一旦发现特征，稍微缓存后续片段，直到能够确认是一个完整的 JSON 块。
