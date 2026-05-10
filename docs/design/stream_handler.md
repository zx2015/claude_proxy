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
