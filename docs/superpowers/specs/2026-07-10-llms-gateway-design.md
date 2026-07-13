# LLM Gateway 与 Agent 目录边界重构设计

日期：2026-07-10

状态：已确认

## 背景

当前 `src/agent/app/models/` 同时承担模型供应商配置、模型系列路由、LangChain 客户端创建、thinking 参数翻译和 Qwen `reasoning_content` 响应适配。Node 直接 import 模型工厂并调用具体 LangChain 模型，因此存在以下问题：

- Node 依赖模型实现层，测试需要 monkeypatch 具体工厂符号。
- `config["model"]` 只写入状态和模型调用摘要，没有传入真实模型工厂，实际模型与审计模型可能不一致。
- Node 直接读取供应商响应字段 `additional_kwargs["reasoning_content"]`，统一响应边界尚未建立。
- `models/model_options.py` 被 State 反向依赖，跨层契约归属于实现层。
- `nodes/` 中混有图片消息 helper、reasoning 日志 helper 和 runtime 配置适配，不全是 LangGraph Node。

本次重构把 `models` 升级为 `llms` Gateway，并同步调整 Agent 内部目录，使 Node 只依赖中立抽象契约。

## 目标

1. Node 只依赖 `agent.app.contracts.llm.LLMGateway`，不 import `agent.app.llms` 或供应商实现。
2. Gateway 统一模型创建、异步调用、耗时统计、模型身份、reasoning 提取、token usage 和错误包装。
3. Gateway 返回稳定 `LLMResponse`，Node 不读取供应商私有响应字段。
4. 实际调用模型与 `model_calls[*].model`、State 模型名来自同一个 Gateway 响应。
5. Graph 作为装配入口，通过 Node 工厂显式注入 Gateway；测试可以直接注入 Fake Gateway。
6. 删除旧 `agent.app.models`，不保留兼容层。
7. 整理 `nodes/`，使该目录只保存主要 Node 及其紧邻的私有实现，不继续承载跨节点基础设施 helper。
8. 后端 Service DTO、HTTP API、数据库结构和现有 LangGraph 对外图名称保持不变。

## 非目标

- 本次不设计自定义消息协议；Gateway 输入继续使用 LangChain `BaseMessage`。
- 本次不移除 `MessagesState`、`HumanMessage` 或 `AIMessage`。
- 本次不拆分生成 Graph 与 Review Graph，不改变现有生成/评审业务流程。
- 本次不接入新的模型供应商，不进行带真实密钥的外部模型调用。
- 本次不修改 reasoning 的数据库存储契约和对外 API 可见性。

## 方案比较

### 方案一：显式注入 Gateway，保留 LangChain 消息类型

Node 构造 `BaseMessage`，调用抽象 `LLMGateway`，Gateway 返回统一响应。Graph Builder 注入具体 Gateway。

优点：解决供应商耦合和响应差异，迁移范围可控，现有 LangGraph State 与 Prompt 消息组装无需重写。缺点：Agent 内部仍知道 LangChain 通用消息类型。

结论：采用。

### 方案二：Gateway 同时屏蔽请求和响应消息协议

引入自定义 role、文本、图片和工具调用消息类型，由 Gateway 转换为 LangChain 或供应商协议。

优点：隔离最彻底。缺点：需要重写基础对话图、多模态消息、测试和 State，超出本次目标。

结论：暂不采用；以后出现第二套非 LangChain 运行时再评估。

### 方案三：全局 Gateway 单例

Node 从模块全局变量获取 Gateway，不通过 Graph 注入。

优点：改动最少。缺点：保留隐藏全局依赖，A/B Graph、单元测试和并发配置隔离较差。

结论：不采用。

## 目标目录结构

```text
src/agent/app/
├── contracts/
│   ├── ARCHITECTURE.md
│   ├── __init__.py
│   └── llm.py                  # Gateway Protocol、调用参数、统一响应和错误类型
├── llms/
│   ├── ARCHITECTURE.md
│   ├── __init__.py
│   ├── gateway.py              # LangChainLLMGateway 具体实现
│   ├── client_factory.py       # provider:model 解析和模型系列路由
│   ├── provider_config.py      # API key/base URL 环境变量约定
│   └── families/
│       ├── __init__.py
│       ├── qwen.py
│       ├── glm.py
│       ├── deepseek.py
│       └── openai.py
├── messages/
│   ├── ARCHITECTURE.md
│   ├── __init__.py
│   └── image_content.py        # 复用的多模态图片消息片段
├── observability/
│   ├── ARCHITECTURE.md
│   ├── __init__.py
│   └── model_reasoning.py      # reasoning 的受控日志输出
├── nodes/
│   ├── ARCHITECTURE.md
│   ├── model_node.py
│   ├── generate_glsl_node.py
│   └── review_render_node.py
├── graphs/
├── prompts/
├── parsers/
├── services/
├── states/
└── config/
```

目录迁移规则：

- `models/llm_factory.py` 迁移为 `llms/client_factory.py`。
- `models/provider_config.py` 迁移为 `llms/provider_config.py`。
- `models/qwen_model.py` 等模型系列适配迁移到 `llms/families/`；provider 仍只表示凭据和 base URL 来源。
- `models/model_options.py` 中跨层类型迁移到 `contracts/llm.py`；供应商翻译逻辑留在 `llms/`。
- `nodes/image_content.py` 迁移到 `messages/image_content.py`。
- `nodes/model_reasoning.py` 的 reasoning 提取进入 Gateway，受控日志函数迁移到 `observability/model_reasoning.py`。
- `nodes/model_runtime_options.py` 只有 `model_node.py` 一个消费者，合并回 `model_node.py` 的私有 helper，避免为单一消费者保留公共模块。
- 删除整个 `models/`，并同步修改 `pyproject.toml` 显式包列表。

## 公共契约

`contracts/llm.py` 是 Node、State、Graph 和 LLM 实现共同依赖的中立边界，不依赖 `nodes`、`graphs`、`services` 或具体供应商模块。

```python
from dataclasses import dataclass
from typing import Literal, Protocol, Sequence

from langchain_core.messages import AIMessage, BaseMessage

ThinkingMode = Literal["default", "on", "off"]


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class LLMCallOptions:
    model_ref: str
    temperature: float = 0
    thinking: ThinkingMode = "default"
    capture_reasoning: bool | None = None


@dataclass(frozen=True)
class LLMResponse:
    message: AIMessage
    text: str
    reasoning_content: str | None
    model_ref: str
    latency_ms: int
    usage: TokenUsage | None = None


class LLMGateway(Protocol):
    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        options: LLMCallOptions,
    ) -> LLMResponse: ...
```

约束：

- Node 记录模型名时只使用 `LLMResponse.model_ref`。
- Gateway 返回的 `model_ref` 必须是本次实际创建客户端时使用的完整引用。
- `print_reasoning` 是 Node/Observability 策略，不属于 `LLMCallOptions`。
- Gateway 不读取 LangGraph State、Runtime Context 或后端对象。
- `LLMResponse` 不暴露供应商原始响应对象。

## Gateway 实现

`LangChainLLMGateway` 的单次调用流程：

1. 校验并规范化 `LLMCallOptions`。
2. 调用 `client_factory`，按 `model_ref` 解析 provider 和 model family。
3. 把 thinking、capture reasoning 等语义选项翻译为供应商配置。
4. 记录开始时间并执行 LangChain 客户端 `ainvoke()`。
5. 从通用 `AIMessage` 和供应商适配结果中提取文本、reasoning、usage。
6. 返回统一 `LLMResponse`。

模型系列适配器只负责客户端创建和系列差异；provider 配置只负责凭据和 base URL。二者都不知道 Node、Prompt、State、Graph 或业务阶段名称。

## Node 与 Graph 装配

三个现有 Node 都改为 Gateway 注入：

- `make_model_node(gateway)` 创建基础对话 Node。
- `make_generate_glsl_node(gateway, config)` 创建生成 Node。
- `make_review_render_node(gateway, config)` 创建评审 Node。

Node 继续负责 Prompt 选择、LangChain 消息组装、Parser 调用和 partial State 映射。模型创建、调用计时、响应差异和真实模型身份全部交给 Gateway。

Graph 文件提供 Builder，并保留现有模块级导出：

```text
build_main_graph(gateway) -> graph
build_shader_generation_graph(gateway) -> shader_generation_graph
```

模块级默认图使用真实 `LangChainLLMGateway`；测试调用 Builder 注入 Fake Gateway。现有 `langgraph.json` 图名和入口对象名称不变。

## 配置作用域

- 环境变量：部署级默认模型、供应商 API key、base URL、Qwen 默认能力。
- `LLMCallOptions`：一次 Gateway 调用的模型引用和语义参数。
- Runtime Context：由基础对话 Node 映射成一次调用的 `LLMCallOptions`。
- Shader Node Config：由 Graph 装配时映射成一次调用的 `LLMCallOptions`。
- State：只保存业务中间结果和结构化调用摘要，不保存运行配置对象。

所有路径最终都必须生成一个完整 `LLMCallOptions.model_ref`，不再依赖仅使用默认模型的 `shader_gen_model()` 便捷入口。

## 统一错误

`contracts/llm.py` 定义稳定错误基类和三个错误类别：

- `LLMConfigurationError`：缺少密钥、模型引用非法、供应商不支持；不可重试。
- `LLMInvocationError`：超时、限流、网络或供应商调用失败；带 `retryable` 标记。
- `LLMResponseError`：响应类型不符合预期或无法规范化；默认不可重试。

错误可以携带 `model_ref`、provider、错误类别和 `retryable`，但不得包含 API key、完整原始响应、base64 图片或 reasoning。Node 不吞掉 Gateway 错误，由 Graph 重试策略或现有后端错误边界处理。

## Reasoning 与可观测性

- Provider/Gateway 负责从供应商响应中规范化 `reasoning_content`。
- Node 根据自己的 `print_reasoning` 配置决定是否调用 `observability.model_reasoning` 输出日志。
- Node 根据现有业务契约把 reasoning 放入 `model_calls`，后端继续写入受控数据库字段。
- Gateway 错误、普通日志和 `LLMResponse` 的公开字符串表示不得自动包含 reasoning。

## 测试设计

### Gateway 单元测试

- 使用 Fake LangChain Client 验证 `text`、reasoning、latency、usage 和 `model_ref` 统一结果。
- 验证传给 Client Factory 的 `model_ref` 与返回结果一致。
- 验证配置、调用和响应错误的统一包装，不泄漏敏感内容。

### Provider 与 Factory 测试

- 迁移现有 provider/model-family 路由测试。
- 迁移 Qwen thinking、capture reasoning 和响应字段保留测试。
- 保留 GLM、DeepSeek、OpenAI-compatible 的凭据和 base URL 测试。

### Node 测试

- 使用 Fake Gateway，不再 monkeypatch `shader_gen_model`。
- 验证 Node 传入的消息、`LLMCallOptions`、Parser 输出和 partial State。
- 明确验证自定义模型同时进入 Gateway、State 和 `model_calls`。
- 验证 `print_reasoning=False` 只禁止日志，不改变已启用的 reasoning 摘要。

### Graph 与边界测试

- 通过 Graph Builder 注入 Fake Gateway，验证节点调用顺序。
- 禁止 `nodes/` import `agent.app.llms`、`agent.app.models` 或供应商模块。
- 禁止 `states/` import `llms`、`nodes`、`graphs`、`services`。
- 禁止后端越过 `agent.app.services.*`。
- 验证旧 `agent.app.models` 包已经删除，`pyproject.toml` 包发现包含新增目录。

### 验证命令

```text
uv run pytest tests/unit_tests
uv run pytest tests/integration_tests
uv run ruff check src/agent backend tests scripts
make docs-check
uv run langgraph validate
make check
```

自动化测试不调用真实外部模型。

## 兼容性与文档

- `agent.app.services.shader_generation` 的函数和 dataclass 保持不变。
- 后端 API 请求/响应 schema、SQL 和前端调用保持不变。
- `langgraph.json` 的 `agent`、`shader_generation` 名称和入口变量保持不变。
- 不保留 `agent.app.models` 导入兼容层；仓库内导入和测试一次性迁移。
- 同步更新 `docs/ARCHITECTURE.md`、`docs/DECISIONS.md`、`PROGRESS.md`、`src/agent/README.md`、`src/agent/ARCHITECTURE.md`、`src/agent/app/ARCHITECTURE.md` 及相关子目录 `ARCHITECTURE.md`。
- 功能行为和验收状态不变，因此不修改 `docs/FEATURES.md` 的状态。

## 实施顺序

1. 先添加 Gateway 契约、Fake Gateway 测试和预期失败的架构边界测试。
2. 创建 `llms/`、`llms/families/` 并迁移模型实现。
3. 实现 `LangChainLLMGateway` 和统一错误/响应。
4. 改造 Node 工厂，使其只依赖 `LLMGateway`。
5. 改造 Graph Builder 并保持现有导出入口。
6. 迁移非 Node helper，删除旧 `models/`。
7. 更新显式包配置、架构文档、决策记录和进度记录。
8. 运行完整验证并记录仍未覆盖的真实模型调用缺口。

## 验收标准

- `src/agent/app/models/` 不再存在。
- Node 源码不 import `agent.app.llms` 或任何供应商实现。
- 三个 Node 都通过构造参数使用 `LLMGateway`。
- `LLMResponse.model_ref` 是 State 与 `model_calls` 的模型名唯一来源。
- 供应商私有 reasoning 提取不再出现在 Node。
- 测试可通过 Fake Gateway 覆盖 Node 和 Graph，无需 monkeypatch 具体模型工厂。
- 后端公共接口、API 响应和现有前端行为不变。
- 所列验证命令全部通过；不执行真实带密钥模型调用。
