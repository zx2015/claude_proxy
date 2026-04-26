# 功能需求文档 (Functional Requirements)

## 1. 项目背景
本项目 `claude_proxy` 是一个基于 Python 的协议适配器，部署在 Claude Code CLI 与 LiteLLM 之间。其核心目标是将 LiteLLM 的输出（特别是工具调用）完美对齐到 Anthropic 协议规范中。

## 2. 核心功能需求

### 2.1 协议适配与转发
- **技术栈**：使用 Python 3.10+ 和 FastAPI 框架。
- **LiteLLM 集成**：通过 LiteLLM 提供的一致性接口进行上游通信。
- **直接模型发现**：直接从 LiteLLM 的 `/v1/models` 接口透传可用模型列表，无需本地映射。

### 2.2 响应重构 (核心)
- **Tool Use 强制对齐**：拦截模型输出，将 OpenAI `tool_calls` 或文本中的工具指令转换为 Anthropic `tool_use` 块。
- **Stop Reason 修正**：确保工具调用时返回 `stop_reason: tool_use`。

### 2.3 错误处理与对齐
- **异常捕获**：必须捕获所有与上游 LiteLLM 通信的异常（超时、连接失败、协议错误）。
- **错误映射**：将 LiteLLM 返回的 HTTP 错误码映射为符合 Anthropic 规范的 JSON 错误响应。

### 2.4 安全与配置
- **双向鉴权**：入站校验 `PROXY_API_KEY`，出站附加 `LITELLM_API_KEY`。
- **监听配置**：支持配置监听的 `HOST` 和 `PORT`。

## 3. 详细配置需求
- **LITELLM_URL**: 上游 LiteLLM 的 API 基础地址。
- **LITELLM_API_KEY**: 访问 LiteLLM 的凭证。
- **PROXY_API_KEY**: 供 Claude Code 使用的凭证。
- **HOST/PORT**: 代理服务监听地址。
