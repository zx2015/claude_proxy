# 协议转换引擎设计 (Transformer)

## 1. 目标
识别并转换来自 LiteLLM 的各种响应格式，确保输出符合 Anthropic 严格的 `tool_use` 规范。

## 2. 架构：插件化转换逻辑
为了应对不同模型输出格式的微差，引擎采用“核心框架 + 模型适配器”的架构。

- **Transformer 核心**: 负责整体流程控制、响应重构和 SSE 流式监测。
- **Model Adapter (插件)**: 负责具体模型的启发式识别（正则）、文本清洗、字段映射以及 **系统提示词增强 (System Prompt Injection)**。

## 3. 增强逻辑：System Prompt Injection
为了提升非 Claude 模型对 Anthropic 工具协议的依从性，适配器可以在请求阶段动态注入指令。
- **机制**: 在将请求转发至上游前，代理获取匹配的适配器，调用 `inject_system_prompt` 方法。
- **典型应用**: 
    - 针对 Qwen：注入强制 XML 格式要求。
    - 针对推理模型：注入思考过程与指令分离的要求。

## 3. 转换场景

### 场景 A：从 OpenAI `tool_calls` 对象转换 (结构化路径)
当通过 LiteLLM 的 OpenAI 兼容接口获取到结构化数据时：
- **输入**: OpenAI `choices[0].message.tool_calls`。
- **转换逻辑**: 将其映射至 Anthropic 的 `tool_use` 块，确保 `input` 是已解析的 JSON 对象。

### 场景 B：从文本块中提取 (启发式路径)
即使在工具模式下，某些模型仍可能在 `text` 块中返回指令。
- **识别模式**: 
    - **XML 模式**: `<tool_code>...</tool_code>` (Claude 风格)
    - **Markdown 模式**: ` ```json ... ``` ` (主流开源模型风格)
- **处理方式**: 由适配器执行正则提取，核心负责将提取后的结果插入 `content` 数组。

## 4. 文本清洗与字段规范化
- **文本清洗**: 在生成 `tool_use` 块后，必须对原始 `text` 块执行清洗，移除已提取的标签或代码块。
- **字段规范化**: 
    - 确保 `id` 具备 `toolu_` 前缀。
    - 无论上游返回何种停止原因，只要存在工具调用，必须强制设置 `stop_reason: "tool_use"`。

## 5. 伪代码逻辑 (逻辑架构)
```python
def transform_response(response_data):
    adapter = factory.get_adapter(response_data['model'])
    for block in response_data['content']:
        if block['type'] == 'text':
            tools, cleaned_text = adapter.detect_tool_calls(block['text'])
            if tools:
                # 重组 content 数组，插入 tool_use 块
                # 修正 stop_reason 为 tool_use
    return response_data
```
