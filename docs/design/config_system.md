# 配置与安全设计 (Config & Security)

## 1. 环境变量配置
系统采用 `.env` 文件或系统环境变量进行配置：

| 变量名 | 必填 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `LITELLM_URL` | 是 | - | LiteLLM API 地址 |
| `LITELLM_API_KEY` | 否 | `sk-empty` | 访问 LiteLLM 的凭证 |
| `PROXY_API_KEY` | 是 | - | Claude Code 访问本服务时必须匹配此 Key |
| `HOST` | 否 | `0.0.0.0` | 服务监听地址 |
| `PORT` | 否 | `8000` | 服务监听端口 |

## 2. 动态模型发现 (Model Discovery)
- **机制**: 代理直接透传 `LITELLM_URL/v1/models` 的返回结果。
- **一致性**: 客户端使用的模型标识符必须与 LiteLLM 注册的 ID 保持一致。

## 3. 模型适配器系统 (Model Adapters)
为了应对不同底层模型的微小差异，设计插件化适配器：
- 适配器匹配逻辑：根据请求中的 `model` 字段（LiteLLM ID）进行关键字匹配。

## 4. 安全鉴权
- **Dependency 注入**: 使用 FastAPI 的 `Depends` 在全局路由上强制执行 `PROXY_API_KEY` 校验。
- **Header 转发**: 自动将 `LITELLM_API_KEY` 注入到发往 LiteLLM 的请求头中。
