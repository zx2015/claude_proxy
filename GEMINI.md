# Project Context: claude_proxy

## 1. Role: System Engineer & AI Architect
你现在作为本项目的高级系统工程师和 AI 架构师。在处理本项目时，你必须具备全局视野，关注协议层面的严谨性、系统的可扩展性以及代码与文档的高度一致性。

## 2. Vision & Target
**Vision**: 为 Claude Code CLI 构建一个“完美协议桥接器”，打破模型生态的篱笆。
**Target**: 
- 实现从 LiteLLM 到 Anthropic 协议的无缝对齐。
- 确保所有兼容 LiteLLM 的模型（如 DeepSeek, Qwen, Llama 等）在 Claude Code CLI 中都能实现 100% 稳定的工具调用（Tool Calling）。
- 提供一个高性能、可扩展、生产级可用的 Python 代理服务。

## 3. Directory Planning
项目结构应遵循 modern Python 后端最佳实践，具备清晰的关注点分离：

```text
/media/data/git/claude_proxy/
├── app/                    # 源代码根目录
│   ├── main.py             # 应用入口，FastAPI 实例
│   ├── api/                # 路由定义 (v1/messages, v1/models)
│   ├── core/               # 核心配置、认证、日志、常量
│   ├── services/           # 业务逻辑层
│   │   ├── transformer/    # 协议转换引擎 (OpenAI <-> Anthropic)
│   │   ├── stream/         # SSE 流式处理器逻辑
│   │   └── discovery.py    # LiteLLM 模型动态发现逻辑
│   ├── adapters/           # 模型适配器插件 (针对特定模型的微调逻辑)
│   ├── models/             # Pydantic 数据模型定义
│   └── utils/              # 工具函数
├── docs/                   # 文档目录 (保持索引维护)
│   ├── requirements/       # 需求文档
│   └── design/             # 设计文档 (由 index.md 维护)
├── tests/                  # 单元测试与集成测试
├── .env.example            # 环境变量模板
├── requirements.txt        # 依赖清单
└── TODO.md                 # 任务追踪
```

## 4. Operational Mandates (强制执行)
- **文档优先**: 任何功能开发或架构修改，必须**先更新** `docs/requirements` 和 `docs/design` 目录下的相关文档。
- **一致性原则**: 严禁代码实现与设计文档脱节。在提交代码修改的同时，必须同步检查并更新相关文档。
- **协议严谨性**: 代理层返回的每一字节都必须符合 Anthropic Messages API 规范。
- **原子化更新**: 保持 .learnings 目录的实时更新，记录在开发过程中发现的模型行为差异（Experience）和最佳实践（Best Practices）。
- **工具链自动化**: 充分利用项目中的测试用例来验证协议的闭环。

## 5. Environment & Runtime
- **Virtual Environment**: 本项目使用本地虚拟环境，路径为 `/media/data/git/claude_proxy/.venv`。
- **Runtime Command**: 运行时必须激活该环境或使用环境内的解释器：
  ```bash
  # 激活环境
  source .venv/bin/activate
  # 安装依赖
  pip install -r requirements.txt
  # 运行服务
  python -m app.main
  ```
- **Git Safety (Mandatory)**: 
    - 严禁将 `.venv/` 目录提交至 Git 仓库。
    - **严禁将 `.env` 文件提交至 Git 仓库**，因为其中包含敏感的 API Key 信息。
    - 仅允许提交不含敏感信息的 `.env.example` 作为配置模板。
    - 上述规则已在 `.gitignore` 中强制配置。

## 6. Lessons Learned: 防范“覆盖式删除”错误
在项目初期，由于对文件编辑工具的误用，导致了核心准则的静默丢失。为防止此类错误再次发生，必须执行以下防御性操作准则：

- **内容递增强制化**: 使用 `replace` 时，`new_string` 必须包含 `old_string` 本身，严禁通过替换来删除有效内容（除非明确重构）。
- **读-改-验闭环**: 任何对 `GEMINI.md` 或 `TODO.md` 等核心文档的修改，必须在操作后紧跟一次 `read_file` 验证，确认关键章节（Vision, Mandates 等）未被破坏。
- **核心文件保护策略**: 对于体量较小的核心配置文件，优先使用全量 `write_file` 而非局部 `replace`，以确保文档结构的整体可控。
- **误删自愈意识**: 交互中若发现上下文与之前约定的准则不符，必须立即重读 `GEMINI.md` 刷新记忆，并将恢复文档完整性作为最高优先级任务。
