# LLM Gateway Implementation Plan

> 归档状态：历史实施基线，不得按下方 checkbox 或 worker 指令重新实施。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `agent.app.models` 重构为显式注入的 `llms` Gateway，使 Node 只依赖中立契约并获得统一模型响应。

**Architecture:** 在 `agent.app.contracts.llm` 定义调用参数、统一响应、错误和 `LLMGateway` Protocol；`agent.app.llms` 实现 LangChain Gateway、客户端工厂、provider 配置和 model-family 适配器。Graph Builder 负责把具体 Gateway 注入 Node，Node 继续使用 LangChain `BaseMessage`，但不再直接创建模型或读取供应商私有响应字段。

**Tech Stack:** Python 3.10+、LangGraph 1.2.7、LangChain Core、langchain-openai 1.3.3、pytest、ruff、FastAPI。

## Global Constraints

- 保留 LangChain `BaseMessage`、`AIMessage`、`HumanMessage` 和现有 `MessagesState`。
- 删除 `src/agent/app/models/`，不保留 `agent.app.models` 兼容层。
- Node 不得 import `agent.app.llms`、`agent.app.models` 或任何 model-family 实现。
- provider 只表示凭据和 base URL；Qwen、GLM、DeepSeek、OpenAI 继续作为 model family 分开处理。
- `LLMResponse.model_ref` 是 State 与 `model_calls[*].model` 的唯一模型身份来源。
- 后端 Service DTO、HTTP API、数据库 schema、前端调用和 `langgraph.json` 图名保持不变。
- 自动化测试不得调用真实外部模型或读取真实 API key。
- 文档、计划、代码注释保持中文，必要的类型和 API 名称保留英文。
- 当前工作区没有 `.git` 元数据；所有任务使用测试检查点代替 commit，不执行 Git 写操作。

---

## File Map

**Create**

- `src/agent/app/contracts/__init__.py`：中立契约包入口。
- `src/agent/app/contracts/llm.py`：Gateway Protocol、调用参数、统一响应、usage 和错误类型。
- `src/agent/app/contracts/ARCHITECTURE.md`：契约依赖规则。
- `src/agent/app/llms/__init__.py`：LLM 实现包入口。
- `src/agent/app/llms/gateway.py`：`LangChainLLMGateway`。
- `src/agent/app/llms/client_factory.py`：provider/model-family 路由和客户端创建。
- `src/agent/app/llms/provider_config.py`：provider 环境配置。
- `src/agent/app/llms/families/__init__.py`：model-family 包入口。
- `src/agent/app/llms/families/qwen.py`：Qwen thinking/reasoning 适配。
- `src/agent/app/llms/families/glm.py`：GLM 客户端适配。
- `src/agent/app/llms/families/deepseek.py`：DeepSeek 客户端适配。
- `src/agent/app/llms/families/openai.py`：OpenAI 客户端适配。
- `src/agent/app/llms/ARCHITECTURE.md`：Gateway 与实现边界。
- `src/agent/app/messages/__init__.py`：消息 helper 包入口。
- `src/agent/app/messages/image_content.py`：图片 data URL 消息片段。
- `src/agent/app/messages/ARCHITECTURE.md`：消息 helper 边界。
- `src/agent/app/observability/model_reasoning.py`：受控 reasoning 日志。
- `tests/unit_tests/test_llm_contract.py`：契约测试。
- `tests/unit_tests/test_llm_gateway.py`：Gateway 统一响应和错误测试。
- `tests/unit_tests/test_agent_architecture_boundaries.py`：目录和依赖方向测试。

**Modify**

- `src/agent/app/config/model_config.py`：增加冻结的 `NodeModelConfig`。
- `src/agent/app/states/agent_state.py`：从 contracts 导入 `ThinkingMode`。
- `src/agent/app/nodes/model_node.py`：改为 Gateway 注入并内联 Runtime 适配。
- `src/agent/app/nodes/generate_glsl_node.py`：使用统一响应。
- `src/agent/app/nodes/review_render_node.py`：使用统一响应。
- `src/agent/app/graphs/main_graph.py`：增加 Graph Builder 和默认 Gateway 装配。
- `src/agent/app/graphs/shader_generation_graph.py`：增加 Graph Builder 和默认 Gateway 装配。
- `tests/unit_tests/test_configuration.py`：迁移现有模型、Node、Graph 测试。
- `tests/integration_tests/test_graph.py`：通过 Builder 注入 Fake Gateway。
- `tests/unit_tests/test_harness_contracts.py`：更新旧 models 边界并覆盖 llms/contracts。
- `scripts/docs_check.py`：更新 Agent service/backend 禁止依赖前缀。
- `pyproject.toml`：注册 contracts、llms、llms.families、messages，删除 models。
- Agent 和全局架构文档、决策记录、进度记录。

**Delete after consumers migrate**

- `src/agent/app/models/`
- `src/agent/app/nodes/image_content.py`
- `src/agent/app/nodes/model_reasoning.py`
- `src/agent/app/nodes/model_runtime_options.py`

---

### Task 1: 建立中立 LLM 契约

**Files:**

- Create: `src/agent/app/contracts/__init__.py`
- Create: `src/agent/app/contracts/llm.py`
- Create: `tests/unit_tests/test_llm_contract.py`

**Interfaces:**

- Produces: `ThinkingMode`、`TokenUsage`、`LLMCallOptions`、`LLMResponse`、`LLMGateway`。
- Produces: `LLMGatewayError`、`LLMConfigurationError`、`LLMInvocationError`、`LLMResponseError`。

- [ ] **Step 1: 写契约失败测试**

```python
import pytest
from langchain_core.messages import AIMessage

from agent.app.contracts.llm import (
    LLMCallOptions,
    LLMConfigurationError,
    LLMResponse,
    TokenUsage,
)


def test_llm_call_options_reject_invalid_values() -> None:
    with pytest.raises(ValueError, match="model_ref"):
        LLMCallOptions(model_ref="   ")
    with pytest.raises(ValueError, match="thinking"):
        LLMCallOptions(model_ref="openai:gpt-4.1", thinking="invalid")
    with pytest.raises(ValueError, match="capture_reasoning"):
        LLMCallOptions(
            model_ref="openai:gpt-4.1",
            capture_reasoning="yes",
        )


def test_llm_response_keeps_normalized_metadata() -> None:
    message = AIMessage(content="完成")
    response = LLMResponse(
        message=message,
        text="完成",
        reasoning_content=None,
        model_ref="openai:gpt-4.1",
        latency_ms=12,
        usage=TokenUsage(input_tokens=3, output_tokens=2, total_tokens=5),
    )
    assert response.message is message
    assert response.model_ref == "openai:gpt-4.1"
    assert response.usage is not None
    assert response.usage.total_tokens == 5


def test_gateway_error_exposes_safe_metadata() -> None:
    error = LLMConfigurationError(
        "LLM 配置无效。",
        model_ref="dashscope:qwen3.7-plus",
        provider="dashscope",
    )
    assert str(error) == "LLM 配置无效。"
    assert error.model_ref == "dashscope:qwen3.7-plus"
    assert error.provider == "dashscope"
    assert error.retryable is False
```

- [ ] **Step 2: 运行测试确认缺少契约包**

Run: `uv run pytest tests/unit_tests/test_llm_contract.py -q`

Expected: collection FAIL with `ModuleNotFoundError: No module named 'agent.app.contracts'`。

- [ ] **Step 3: 实现契约**

```python
# src/agent/app/contracts/__init__.py
"""Agent 跨模块中立契约."""
```

```python
# src/agent/app/contracts/llm.py
"""LLM Gateway 的中立调用契约."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from langchain_core.messages import AIMessage, BaseMessage

ThinkingMode = Literal["default", "on", "off"]
THINKING_MODES = {"default", "on", "off"}


def normalize_thinking_mode(value: ThinkingMode | str | None) -> ThinkingMode:
    """规范化模型 thinking 语义值."""
    normalized = "default" if value is None else value
    if normalized not in THINKING_MODES:
        raise ValueError("thinking 只能配置为 default/on/off。")
    return cast(ThinkingMode, normalized)


@dataclass(frozen=True)
class TokenUsage:
    """统一 token 使用量."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class LLMCallOptions:
    """单次 LLM 调用的语义参数."""

    model_ref: str
    temperature: float = 0
    thinking: ThinkingMode | str | None = "default"
    capture_reasoning: bool | None = None

    def __post_init__(self) -> None:
        if not self.model_ref.strip():
            raise ValueError("model_ref 不能为空。")
        object.__setattr__(self, "thinking", normalize_thinking_mode(self.thinking))
        if self.capture_reasoning is not None and not isinstance(
            self.capture_reasoning, bool
        ):
            raise ValueError("capture_reasoning 只能配置为 true/false。")


@dataclass(frozen=True)
class LLMResponse:
    """供应商无关的 LLM 响应."""

    message: AIMessage
    text: str
    reasoning_content: str | None
    model_ref: str
    latency_ms: int
    usage: TokenUsage | None = None


class LLMGateway(Protocol):
    """Node 可依赖的唯一 LLM 调用接口."""

    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        options: LLMCallOptions,
    ) -> LLMResponse: ...


class LLMGatewayError(RuntimeError):
    """统一 LLM 错误基类."""

    def __init__(
        self,
        message: str,
        *,
        model_ref: str,
        provider: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.model_ref = model_ref
        self.provider = provider
        self.retryable = retryable


class LLMConfigurationError(LLMGatewayError):
    """LLM 配置错误."""

    def __init__(self, message: str, *, model_ref: str, provider: str | None) -> None:
        super().__init__(
            message,
            model_ref=model_ref,
            provider=provider,
            retryable=False,
        )


class LLMInvocationError(LLMGatewayError):
    """LLM 外部调用错误."""


class LLMResponseError(LLMGatewayError):
    """LLM 响应规范化错误."""
```

- [ ] **Step 4: 运行契约测试**

Run: `uv run pytest tests/unit_tests/test_llm_contract.py -q`

Expected: `3 passed`。

- [ ] **Step 5: 检查点**

Run: `uv run ruff check src/agent/app/contracts tests/unit_tests/test_llm_contract.py`

Expected: exit 0。

---

### Task 2: 迁移客户端工厂和 model-family 适配器

**Files:**

- Create: `src/agent/app/llms/__init__.py`
- Create: `src/agent/app/llms/provider_config.py`
- Create: `src/agent/app/llms/client_factory.py`
- Create: `src/agent/app/llms/families/__init__.py`
- Create: `src/agent/app/llms/families/qwen.py`
- Create: `src/agent/app/llms/families/glm.py`
- Create: `src/agent/app/llms/families/deepseek.py`
- Create: `src/agent/app/llms/families/openai.py`
- Modify: `tests/unit_tests/test_configuration.py`

**Interfaces:**

- Consumes: `LLMCallOptions` from Task 1。
- Produces: `create_chat_model(options: LLMCallOptions) -> BaseChatModel`。
- Produces: `_split_model_reference()` and `_model_family()` as private routing helpers。

- [ ] **Step 1: 把现有模型工厂测试切换到新路径**

Replace the model imports and helper in `tests/unit_tests/test_configuration.py` with:

```python
from agent.app.config.model_config import SHADER_GEN_MODEL_NAME
from agent.app.contracts.llm import LLMCallOptions
from agent.app.llms import client_factory


def model_family_module(name: str):
    return importlib.import_module(f"agent.app.llms.families.{name}")


def test_llm_client_factory_configured() -> None:
    assert SHADER_GEN_MODEL_NAME == "dashscope:qwen3.7-plus"
    assert callable(client_factory.create_chat_model)
```

Update the routing assertions to call:

```python
client_factory.create_chat_model(
    LLMCallOptions(
        model_ref="dashscope:qwen3.7-plus",
        temperature=0.1,
        thinking="on",
        capture_reasoning=True,
    )
)
```

The monkeypatched Qwen family function must receive `model="qwen3.7-plus"`, `provider="dashscope"`, `temperature=0.1`, `thinking="on"`, and `capture_reasoning=True`。

- [ ] **Step 2: 运行目标测试确认新包缺失**

Run: `uv run pytest tests/unit_tests/test_configuration.py -q`

Expected: collection FAIL with `ModuleNotFoundError: No module named 'agent.app.llms'`。

- [ ] **Step 3: 创建 provider 配置和普通 family 适配器**

`provider_config.py` 保留现有 `ProviderSettings`、`ProviderEnv`、`PROVIDER_ENVS`、`PROVIDER_NAMES` 和 `provider_settings()` 行为。三个普通 family 文件使用以下完整形态：

```python
# src/agent/app/llms/families/glm.py
"""GLM model-family 客户端工厂."""

from langchain_openai import ChatOpenAI

from agent.app.llms.provider_config import provider_settings


def get_glm_model(
    model: str,
    provider: str | None = None,
    temperature: float = 0,
) -> ChatOpenAI:
    settings = provider_settings(provider, default_provider="glm")
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        api_key=settings.api_key,
        base_url=settings.base_url,
    )
```

```python
# src/agent/app/llms/families/deepseek.py
"""DeepSeek model-family 客户端工厂."""

from langchain_openai import ChatOpenAI

from agent.app.llms.provider_config import provider_settings


def get_deepseek_model(
    model: str,
    provider: str | None = None,
    temperature: float = 0,
) -> ChatOpenAI:
    settings = provider_settings(provider, default_provider="deepseek")
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        api_key=settings.api_key,
        base_url=settings.base_url,
    )
```

```python
# src/agent/app/llms/families/openai.py
"""OpenAI model-family 客户端工厂."""

from langchain_openai import ChatOpenAI

from agent.app.llms.provider_config import provider_settings


def get_openai_model(
    model: str,
    provider: str | None = None,
    temperature: float = 0,
) -> ChatOpenAI:
    settings = provider_settings(provider, default_provider="openai")
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        api_key=settings.api_key,
        base_url=settings.base_url,
    )
```

- [ ] **Step 4: 迁移 Qwen family 适配**

Move `QwenChatOpenAI` and `_response_to_dict()` into `llms/families/qwen.py`。Use contract semantics directly:

```python
def _resolve_thinking_enabled(
    thinking: ThinkingMode | str | None,
    default: bool | None,
) -> bool | None:
    normalized = normalize_thinking_mode(thinking)
    if normalized == "on":
        return True
    if normalized == "off":
        return False
    return default


def _resolve_capture_reasoning(value: bool | None, default: bool) -> bool:
    return default if value is None else value


def get_qwen_model(
    model: str,
    provider: str | None = None,
    temperature: float = 0,
    thinking: ThinkingMode | str | None = "default",
    capture_reasoning: bool | None = None,
) -> QwenChatOpenAI:
    settings = provider_settings(provider, default_provider="dashscope")
    enable_thinking = _resolve_thinking_enabled(
        thinking,
        SHADER_GEN_QWEN_ENABLE_THINKING,
    )
    return QwenChatOpenAI(
        model=model,
        temperature=temperature,
        api_key=settings.api_key,
        base_url=settings.base_url,
        extra_body=(
            None if enable_thinking is None else {"enable_thinking": enable_thinking}
        ),
        output_thinking=_resolve_capture_reasoning(
            capture_reasoning,
            SHADER_GEN_QWEN_OUTPUT_THINKING,
        ),
    )
```

- [ ] **Step 5: 实现客户端路由工厂**

```python
# src/agent/app/llms/client_factory.py
"""按 provider 和 model family 创建 LangChain 聊天客户端."""

from langchain_core.language_models.chat_models import BaseChatModel

from agent.app.contracts.llm import LLMCallOptions
from agent.app.llms.families import deepseek, glm, openai, qwen
from agent.app.llms.provider_config import PROVIDER_NAMES

MODEL_FAMILY_PREFIXES = ("qwen:", "glm:", "deepseek:", "openai:")


def create_chat_model(options: LLMCallOptions) -> BaseChatModel:
    provider, model_name = _split_model_reference(options.model_ref)
    family = _model_family(provider, model_name)
    if family == "qwen":
        return qwen.get_qwen_model(
            model_name,
            provider=provider,
            temperature=options.temperature,
            thinking=options.thinking,
            capture_reasoning=options.capture_reasoning,
        )
    if family == "glm":
        return glm.get_glm_model(model_name, provider, options.temperature)
    if family == "deepseek":
        return deepseek.get_deepseek_model(model_name, provider, options.temperature)
    if family == "openai":
        return openai.get_openai_model(model_name, provider, options.temperature)
    raise ValueError(f"无法识别模型系列：{model_name}。")


def _split_model_reference(model_ref: str) -> tuple[str | None, str]:
    prefix, separator, model_name = model_ref.partition(":")
    if not separator:
        return None, model_ref
    if prefix in PROVIDER_NAMES:
        return prefix, model_name
    if model_ref.startswith(MODEL_FAMILY_PREFIXES):
        return None, model_name
    return None, model_ref


def _model_family(provider: str | None, model_name: str) -> str:
    normalized = model_name.lower()
    if normalized.startswith(("qwen", "qwq")):
        return "qwen"
    if normalized.startswith("glm"):
        return "glm"
    if normalized.startswith("deepseek"):
        return "deepseek"
    if normalized.startswith(("gpt", "o1", "o3", "o4")):
        return "openai"
    if provider in {"glm", "deepseek", "openai"}:
        return provider
    if provider is None:
        return "openai"
    raise ValueError(f"{provider}:{model_name} 未声明可用的模型系列。")
```

- [ ] **Step 6: 运行模型配置测试**

Run: `uv run pytest tests/unit_tests/test_configuration.py -q`

Expected: provider、family、Qwen thinking/reasoning 目标测试 PASS；Node 测试仍使用旧实现且保持 PASS。

- [ ] **Step 7: 检查点**

Run: `uv run ruff check src/agent/app/contracts src/agent/app/llms tests/unit_tests/test_configuration.py`

Expected: exit 0。

---

### Task 3: 实现统一 LangChain Gateway

**Files:**

- Create: `src/agent/app/llms/gateway.py`
- Create: `tests/unit_tests/test_llm_gateway.py`

**Interfaces:**

- Consumes: `create_chat_model(options)` from Task 2。
- Produces: `LangChainLLMGateway.ainvoke(messages, options) -> LLMResponse`。

- [ ] **Step 1: 写统一响应和错误失败测试**

```python
import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.app.contracts.llm import (
    LLMCallOptions,
    LLMConfigurationError,
    LLMInvocationError,
    LLMResponseError,
)
from agent.app.llms.gateway import LangChainLLMGateway


@pytest.mark.anyio
async def test_gateway_normalizes_response() -> None:
    class FakeClient:
        async def ainvoke(self, messages):
            assert messages == [HumanMessage(content="你好")]
            return AIMessage(
                content="完成",
                additional_kwargs={"reasoning_content": "推理"},
                usage_metadata={
                    "input_tokens": 3,
                    "output_tokens": 2,
                    "total_tokens": 5,
                },
            )

    times = iter((10.0, 10.125))
    captured = []
    gateway = LangChainLLMGateway(
        client_factory=lambda options: captured.append(options) or FakeClient(),
        clock=lambda: next(times),
    )
    options = LLMCallOptions(
        model_ref="dashscope:qwen3.7-plus",
        thinking="on",
        capture_reasoning=True,
    )
    result = await gateway.ainvoke([HumanMessage(content="你好")], options)
    assert captured == [options]
    assert result.text == "完成"
    assert result.reasoning_content == "推理"
    assert result.model_ref == options.model_ref
    assert result.latency_ms == 125
    assert result.usage is not None
    assert result.usage.total_tokens == 5


@pytest.mark.anyio
async def test_gateway_wraps_configuration_error_without_secret() -> None:
    def fail_factory(options):
        raise ValueError("secret-key")

    gateway = LangChainLLMGateway(client_factory=fail_factory)
    with pytest.raises(LLMConfigurationError) as caught:
        await gateway.ainvoke(
            [HumanMessage(content="你好")],
            LLMCallOptions(model_ref="dashscope:qwen3.7-plus"),
        )
    assert "secret-key" not in str(caught.value)
    assert caught.value.provider == "dashscope"


@pytest.mark.anyio
async def test_gateway_marks_timeout_retryable() -> None:
    class FailingClient:
        async def ainvoke(self, messages):
            raise TimeoutError("timeout")

    gateway = LangChainLLMGateway(client_factory=lambda options: FailingClient())
    with pytest.raises(LLMInvocationError) as caught:
        await gateway.ainvoke(
            [HumanMessage(content="你好")],
            LLMCallOptions(model_ref="openai:gpt-4.1"),
        )
    assert caught.value.retryable is True


@pytest.mark.anyio
async def test_gateway_rejects_non_ai_message() -> None:
    class InvalidClient:
        async def ainvoke(self, messages):
            return HumanMessage(content="错误类型")

    gateway = LangChainLLMGateway(client_factory=lambda options: InvalidClient())
    with pytest.raises(LLMResponseError):
        await gateway.ainvoke(
            [HumanMessage(content="你好")],
            LLMCallOptions(model_ref="openai:gpt-4.1"),
        )
```

- [ ] **Step 2: 运行测试确认 Gateway 缺失**

Run: `uv run pytest tests/unit_tests/test_llm_gateway.py -q`

Expected: collection FAIL for missing `agent.app.llms.gateway`。

- [ ] **Step 3: 实现 Gateway**

```python
# src/agent/app/llms/gateway.py
"""LangChain LLM Gateway 实现."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from time import perf_counter

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage

from agent.app.contracts.llm import (
    LLMCallOptions,
    LLMConfigurationError,
    LLMInvocationError,
    LLMResponse,
    LLMResponseError,
    TokenUsage,
)
from agent.app.llms.client_factory import create_chat_model
from agent.app.llms.provider_config import PROVIDER_NAMES

ClientFactory = Callable[[LLMCallOptions], BaseChatModel]


class LangChainLLMGateway:
    """通过 LangChain 客户端执行统一 LLM 调用."""

    def __init__(
        self,
        client_factory: ClientFactory = create_chat_model,
        clock: Callable[[], float] = perf_counter,
    ) -> None:
        self._client_factory = client_factory
        self._clock = clock

    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        options: LLMCallOptions,
    ) -> LLMResponse:
        provider = _provider_name(options.model_ref)
        try:
            client = self._client_factory(options)
        except Exception as exc:
            raise LLMConfigurationError(
                "LLM 配置无效。",
                model_ref=options.model_ref,
                provider=provider,
            ) from exc

        started_at = self._clock()
        try:
            message = await client.ainvoke(list(messages))
        except Exception as exc:
            raise LLMInvocationError(
                "LLM 调用失败。",
                model_ref=options.model_ref,
                provider=provider,
                retryable=_is_retryable(exc),
            ) from exc
        latency_ms = int((self._clock() - started_at) * 1000)

        if not isinstance(message, AIMessage):
            raise LLMResponseError(
                "LLM 响应类型无效。",
                model_ref=options.model_ref,
                provider=provider,
                retryable=False,
            )
        return LLMResponse(
            message=message,
            text=message.text,
            reasoning_content=_reasoning_content(message),
            model_ref=options.model_ref,
            latency_ms=latency_ms,
            usage=_token_usage(message),
        )


def _provider_name(model_ref: str) -> str | None:
    prefix, separator, _ = model_ref.partition(":")
    return prefix if separator and prefix in PROVIDER_NAMES else None


def _reasoning_content(message: AIMessage) -> str | None:
    value = message.additional_kwargs.get("reasoning_content")
    return str(value) if value else None


def _token_usage(message: AIMessage) -> TokenUsage | None:
    usage = message.usage_metadata
    if not usage:
        return None
    return TokenUsage(
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        total_tokens=usage.get("total_tokens"),
    )


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    name = type(exc).__name__.lower()
    return any(token in name for token in ("timeout", "ratelimit", "connection"))
```

- [ ] **Step 4: 运行 Gateway 和契约测试**

Run: `uv run pytest tests/unit_tests/test_llm_contract.py tests/unit_tests/test_llm_gateway.py -q`

Expected: `7 passed`。

- [ ] **Step 5: 检查点**

Run: `uv run ruff check src/agent/app/contracts src/agent/app/llms tests/unit_tests/test_llm_contract.py tests/unit_tests/test_llm_gateway.py`

Expected: exit 0。

---

### Task 4: 改造基础对话 Node 与 Graph Builder

**Files:**

- Modify: `src/agent/app/states/agent_state.py`
- Modify: `src/agent/app/nodes/model_node.py`
- Modify: `src/agent/app/graphs/main_graph.py`
- Modify: `tests/unit_tests/test_configuration.py`
- Modify: `tests/integration_tests/test_graph.py`

**Interfaces:**

- Consumes: `LLMGateway` and `LLMCallOptions`。
- Produces: `make_model_node(gateway)` and `build_main_graph(gateway)`。
- Preserves: module-level `graph` export for `langgraph.json`；`model_node.py` only exposes `make_model_node()`。

- [ ] **Step 1: 把基础 Node 测试改为 Fake Gateway 注入**

```python
class FakeGateway:
    def __init__(self, response: LLMResponse) -> None:
        self.response = response
        self.calls = []

    async def ainvoke(self, messages, options):
        self.calls.append((messages, options))
        return self.response


@pytest.mark.anyio
async def test_call_model_uses_gateway_runtime_options() -> None:
    input_messages = [HumanMessage(content="你好")]
    output_message = AIMessage(content="你好，我是 ShaderGen。")
    gateway = FakeGateway(
        LLMResponse(
            message=output_message,
            text=output_message.text,
            reasoning_content=None,
            model_ref=SHADER_GEN_MODEL_NAME,
            latency_ms=3,
        )
    )
    node = make_model_node(gateway)

    class FakeRuntime:
        context = {"model_thinking": "off", "capture_reasoning": True}

    result = await node({"messages": input_messages}, FakeRuntime())
    assert result == {"messages": [output_message]}
    _, options = gateway.calls[0]
    assert options.model_ref == SHADER_GEN_MODEL_NAME
    assert options.thinking == "off"
    assert options.capture_reasoning is True
```

Update the integration test to construct `fake_gateway = FakeGateway(LLMResponse(message=output_message, text=output_message.text, reasoning_content=None, model_ref=SHADER_GEN_MODEL_NAME, latency_ms=1))`, call `test_graph = build_main_graph(fake_gateway)`, and invoke `await test_graph.ainvoke({"messages": [HumanMessage(content="你好")]})` rather than monkeypatching `model_node.shader_gen_model`。

- [ ] **Step 2: 运行基础 Node 测试确认工厂缺失**

Run: `uv run pytest tests/unit_tests/test_configuration.py::test_call_model_uses_gateway_runtime_options tests/integration_tests/test_graph.py -q`

Expected: FAIL because `make_model_node` and `build_main_graph` do not exist。

- [ ] **Step 3: 实现 Gateway Node 工厂并内联 Runtime 适配**

```python
# src/agent/app/nodes/model_node.py
"""基础对话图的模型 Node."""

from collections.abc import Mapping
from typing import Any

from langgraph.runtime import Runtime

from agent.app.config.model_config import SHADER_GEN_MODEL_NAME
from agent.app.contracts.llm import LLMCallOptions, LLMGateway
from agent.app.states.agent_state import Context, State

_MISSING = object()


def make_model_node(gateway: LLMGateway):
    """创建只依赖 Gateway 的基础模型 Node."""

    async def call_model(state: State, runtime: Runtime[Context] | None = None):
        response = await gateway.ainvoke(
            state["messages"],
            _model_call_options(runtime),
        )
        return {"messages": [response.message]}

    return call_model


def _model_call_options(runtime: Any | None) -> LLMCallOptions:
    context = None if runtime is None else getattr(runtime, "context", None)
    thinking = _context_value(context, "model_thinking")
    capture_reasoning = _context_value(context, "capture_reasoning")
    return LLMCallOptions(
        model_ref=SHADER_GEN_MODEL_NAME,
        thinking="default" if thinking is _MISSING else thinking,
        capture_reasoning=(
            None if capture_reasoning is _MISSING else capture_reasoning
        ),
    )


def _context_value(context: Any, name: str) -> Any:
    if context is None:
        return _MISSING
    if isinstance(context, Mapping):
        return context.get(name, _MISSING)
    return getattr(context, name, _MISSING)
```

Update `states/agent_state.py` to import `ThinkingMode` from `agent.app.contracts.llm`。

- [ ] **Step 4: 实现 Graph Builder 和默认导出**

```python
# src/agent/app/graphs/main_graph.py
"""构建 ShaderGen 主图."""

from langgraph.graph import START, StateGraph

from agent.app.contracts.llm import LLMGateway
from agent.app.llms.gateway import LangChainLLMGateway
from agent.app.nodes.model_node import make_model_node
from agent.app.states.agent_state import Context, State


def build_main_graph(gateway: LLMGateway):
    call_model = make_model_node(gateway)
    return (
        StateGraph(State, context_schema=Context)
        .add_node("call_model", call_model)
        .add_edge(START, "call_model")
        .compile(name="ShaderGen")
    )


_default_gateway = LangChainLLMGateway()
graph = build_main_graph(_default_gateway)
```

- [ ] **Step 5: 运行基础 Node、Graph 和集成测试**

Run: `uv run pytest tests/unit_tests/test_configuration.py -q && uv run pytest tests/integration_tests/test_graph.py -q`

Expected: all selected tests PASS。

- [ ] **Step 6: 检查点**

Run: `uv run ruff check src/agent/app/nodes/model_node.py src/agent/app/graphs/main_graph.py src/agent/app/states/agent_state.py tests/integration_tests/test_graph.py`

Expected: exit 0。

---

### Task 5: 改造 Shader Nodes 与 Shader Graph Builder

**Files:**

- Modify: `src/agent/app/config/model_config.py`
- Modify: `src/agent/app/nodes/generate_glsl_node.py`
- Modify: `src/agent/app/nodes/review_render_node.py`
- Modify: `src/agent/app/graphs/shader_generation_graph.py`
- Modify: `tests/unit_tests/test_configuration.py`

**Interfaces:**

- Produces: frozen `NodeModelConfig(call: LLMCallOptions, print_reasoning: bool)`。
- Produces: `make_generate_glsl_node(gateway, config)` and `make_review_render_node(gateway, config)`。
- Produces: `build_shader_generation_graph(gateway)`。
- Preserves: module-level `shader_generation_graph` export for `langgraph.json`；Node modules only expose factories and typed default configs。

- [ ] **Step 1: 写实际模型身份回归测试**

Remove direct imports of `generate_glsl` and `review_render` from `tests/unit_tests/test_configuration.py`。Keep the file-boundary assertion as:

```python
def test_shader_node_factories_are_split_by_file() -> None:
    assert make_generate_glsl_node.__module__ == (
        "agent.app.nodes.generate_glsl_node"
    )
    assert make_review_render_node.__module__ == (
        "agent.app.nodes.review_render_node"
    )
```

```python
from dataclasses import replace


@pytest.mark.anyio
async def test_generate_node_uses_gateway_model_identity() -> None:
    class FakeGateway:
        def __init__(self) -> None:
            self.options = None

        async def ainvoke(self, messages, options):
            self.options = options
            message = AIMessage(content="void main() {}")
            return LLMResponse(
                message=message,
                text=message.text,
                reasoning_content=None,
                model_ref="deepseek:deepseek-chat",
                latency_ms=7,
            )

    gateway = FakeGateway()
    config = replace(
        GENERATE_GLSL_MODEL_CONFIG,
        call=replace(
            GENERATE_GLSL_MODEL_CONFIG.call,
            model_ref="deepseek:deepseek-chat",
        ),
    )
    node = make_generate_glsl_node(gateway, config)
    result = await node({"image": b"image", "content_type": "image/png"})
    assert gateway.options is not None
    assert gateway.options.model_ref == "deepseek:deepseek-chat"
    assert result["glsl_model_name"] == "deepseek:deepseek-chat"
    assert result["model_calls"][0]["model"] == "deepseek:deepseek-chat"
```

Update reasoning and graph-order tests to use a sequence Fake Gateway returning `LLMResponse` instances。

- [ ] **Step 2: 运行回归测试确认旧 Node 不接受 Gateway**

Run: `uv run pytest tests/unit_tests/test_configuration.py::test_generate_node_uses_gateway_model_identity -q`

Expected: FAIL because the current Node factory does not accept Gateway and ignores configured model during invocation。

- [ ] **Step 3: 增加冻结 Node 配置**

Add these imports at the top of `src/agent/app/config/model_config.py`, then define the class before module-level model constants:

```python
from dataclasses import dataclass

from agent.app.contracts.llm import LLMCallOptions


@dataclass(frozen=True)
class NodeModelConfig:
    """Node 级 LLM 调用和 reasoning 日志配置."""

    call: LLMCallOptions
    print_reasoning: bool = False
```

Default configs become:

```python
GENERATE_GLSL_MODEL_CONFIG = NodeModelConfig(
    call=LLMCallOptions(
        model_ref=SHADER_GEN_MODEL_NAME,
        thinking="on",
        capture_reasoning=True,
    ),
    print_reasoning=True,
)
```

`REVIEW_RENDER_MODEL_CONFIG` uses the same call defaults。

- [ ] **Step 4: 改造两个 Shader Node 调用统一响应**

Both Node factories accept positional `gateway` and optional typed config. Replace direct model invocation with:

```python
response = await gateway.ainvoke(
    [
        HumanMessage(
            content=[
                {"type": "text", "text": IMAGE_TO_GLSL_PROMPT},
                image_url_part(state["image"], state["content_type"]),
            ]
        )
    ],
    config.call,
)
if config.print_reasoning:
    log_reasoning_content("generate_glsl", response.reasoning_content)
glsl = extract_glsl(response.text)
model_call = {
    "model": response.model_ref,
    "prompt_version": "image_to_glsl",
    "latency_ms": response.latency_ms,
    "output_chars": len(response.text),
    "glsl_chars": len(glsl),
}
if response.reasoning_content:
    model_call["reasoning_content"] = response.reasoning_content
```

The returned generation State must use `response.model_ref` for both `glsl_model_name` and `vision_model_name`。The Review Node uses the same pattern with `parse_shader_review_response(response.text)` and `review_model_name=response.model_ref`。

- [ ] **Step 5: 实现 Shader Graph Builder**

```python
def build_shader_generation_graph(gateway: LLMGateway):
    return (
        StateGraph(ShaderPipelineState, context_schema=Context)
        .add_node(
            "generate_glsl",
            make_generate_glsl_node(gateway, GENERATE_GLSL_MODEL_CONFIG),
        )
        .add_node(
            "review_render",
            make_review_render_node(gateway, REVIEW_RENDER_MODEL_CONFIG),
        )
        .add_edge(START, "generate_glsl")
        .add_conditional_edges("generate_glsl", _next_after_generate)
        .add_edge("review_render", END)
        .compile(name="ShaderGeneration")
    )


_default_gateway = LangChainLLMGateway()
shader_generation_graph = build_shader_generation_graph(_default_gateway)
```

- [ ] **Step 6: 运行 Shader Node 和 Graph 测试**

Run: `uv run pytest tests/unit_tests/test_configuration.py -q`

Expected: all provider、Node、Graph、backend delegation tests PASS。

- [ ] **Step 7: 检查点**

Run: `uv run ruff check src/agent/app/config/model_config.py src/agent/app/nodes src/agent/app/graphs tests/unit_tests/test_configuration.py`

Expected: exit 0。

---

### Task 6: 完成目录迁移并固化依赖边界

**Files:**

- Create: `src/agent/app/messages/__init__.py`
- Create: `src/agent/app/messages/image_content.py`
- Create: `src/agent/app/observability/model_reasoning.py`
- Create: `tests/unit_tests/test_agent_architecture_boundaries.py`
- Modify: Node imports、`pyproject.toml`、`scripts/docs_check.py`、`tests/unit_tests/test_harness_contracts.py`
- Delete: old models package and old Node helper files。

**Interfaces:**

- Produces: `messages.image_content.image_url_part()`。
- Produces: `observability.model_reasoning.log_reasoning_content()`。
- Enforces: Node only depends on `contracts.llm` for model capability。

- [ ] **Step 1: 写目录和 import 边界失败测试**

```python
from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _import_targets(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    targets = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            targets.append(node.module)
            targets.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return targets


def _violations(package: str, forbidden: tuple[str, ...]) -> list[str]:
    violations = []
    for path in sorted((ROOT / "src/agent/app" / package).glob("*.py")):
        for target in _import_targets(path):
            if target.startswith(forbidden):
                violations.append(f"{path.name}: {target}")
    return violations


def test_nodes_use_llm_contract_not_implementation() -> None:
    assert _violations(
        "nodes",
        ("agent.app.llms", "agent.app.models"),
    ) == []


def test_states_do_not_depend_on_agent_implementation_layers() -> None:
    assert _violations(
        "states",
        (
            "agent.app.llms",
            "agent.app.models",
            "agent.app.nodes",
            "agent.app.graphs",
            "agent.app.services",
        ),
    ) == []


def test_agent_package_layout_uses_llms_gateway() -> None:
    assert not (ROOT / "src/agent/app/models").exists()
    packages = set(
        tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
            "tool"
        ]["setuptools"]["packages"]
    )
    assert "agent.app.models" not in packages
    assert {
        "agent.app.contracts",
        "agent.app.llms",
        "agent.app.llms.families",
        "agent.app.messages",
    } <= packages
```

- [ ] **Step 2: 运行边界测试确认旧目录和 import 失败**

Run: `uv run pytest tests/unit_tests/test_agent_architecture_boundaries.py -q`

Expected: FAIL showing old `models/` exists and Node imports concrete model modules。

- [ ] **Step 3: 迁移非 Node helper**

Create `messages/image_content.py` with the current pure `image_url_part()` implementation。Create `observability/model_reasoning.py` with only:

```python
"""模型 reasoning 的受控日志输出."""

import logging

logger = logging.getLogger("agent.model")


def log_reasoning_content(stage: str, reasoning_content: str | None) -> None:
    if reasoning_content:
        logger.info(
            "模型思维链 stage=%s reasoning_content=%s",
            stage,
            reasoning_content,
        )
```

Update both Shader Node imports to the new packages。

- [ ] **Step 4: 更新显式包列表**

Replace `agent.app.models` in `pyproject.toml` with:

```toml
    "agent.app.contracts",
    "agent.app.llms",
    "agent.app.llms.families",
    "agent.app.messages",
```

Keep all existing Agent and Backend packages。

- [ ] **Step 5: 删除旧实现和 helper**

Delete:

```text
src/agent/app/models/__init__.py
src/agent/app/models/llm_factory.py
src/agent/app/models/model_options.py
src/agent/app/models/provider_config.py
src/agent/app/models/qwen_model.py
src/agent/app/models/glm_model.py
src/agent/app/models/deepseek_model.py
src/agent/app/models/openai_model.py
src/agent/app/models/ARCHITECTURE.md
src/agent/app/nodes/image_content.py
src/agent/app/nodes/model_reasoning.py
src/agent/app/nodes/model_runtime_options.py
```

Do not add re-export files or compatibility imports。

- [ ] **Step 6: 更新 docs-check 和 harness 边界**

In `scripts/docs_check.py`, replace service/backend forbidden prefixes containing `agent.app.models` with both `agent.app.llms` and `agent.app.contracts` where backend access is forbidden。For Agent service, forbid direct `nodes` and `llms`; service may continue using graphs、parsers and config。

Update `tests/unit_tests/test_harness_contracts.py` to assert the same prefixes。

- [ ] **Step 7: 运行边界和完整单元测试**

Run: `uv run pytest tests/unit_tests/test_agent_architecture_boundaries.py tests/unit_tests/test_harness_contracts.py -q && uv run pytest tests/unit_tests -q`

Expected: all tests PASS and no collection import references `agent.app.models`。

- [ ] **Step 8: 检查点**

Run:

```bash
! rg -n "agent\.app\.models|src/agent/app/models" src/agent backend scripts pyproject.toml
! rg -n "agent\.app\.llms" src/agent/app/nodes
! test -e src/agent/app/nodes/image_content.py
! test -e src/agent/app/nodes/model_reasoning.py
! test -e src/agent/app/nodes/model_runtime_options.py
```

Expected: all commands exit 0；tests may still contain forbidden-prefix strings as architecture assertions。

---

### Task 7: 同步架构文档、决策和进度

**Files:**

- Create: `src/agent/app/contracts/ARCHITECTURE.md`
- Create: `src/agent/app/llms/ARCHITECTURE.md`
- Create: `src/agent/app/messages/ARCHITECTURE.md`
- Modify: `src/agent/app/observability/ARCHITECTURE.md`
- Modify: `src/agent/app/nodes/ARCHITECTURE.md`
- Modify: `src/agent/app/graphs/ARCHITECTURE.md`
- Modify: `src/agent/app/states/ARCHITECTURE.md`
- Modify: `src/agent/app/config/ARCHITECTURE.md`
- Modify: `src/agent/app/ARCHITECTURE.md`
- Modify: `src/agent/ARCHITECTURE.md`
- Modify: `src/agent/README.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/DECISIONS.md`
- Modify: `PROGRESS.md`

**Interfaces:**

- Documents the enforced direction: `graphs -> nodes -> contracts <- llms`。
- Preserves feature status; `docs/FEATURES.md` remains unchanged。

- [ ] **Step 1: 写文档契约失败断言**

Add to `tests/unit_tests/test_harness_contracts.py`:

```python
def test_agent_docs_describe_llms_gateway_boundary() -> None:
    app_architecture = _read("src/agent/app/ARCHITECTURE.md")
    agent_architecture = _read("src/agent/ARCHITECTURE.md")
    assert "agent.app.contracts" in app_architecture
    assert "agent.app.llms" in app_architecture
    assert "Node 不得直接依赖 `agent.app.llms`" in app_architecture
    assert "LLM Gateway" in agent_architecture
    assert "agent.app.models" not in app_architecture
```

- [ ] **Step 2: 运行文档契约确认旧文档失败**

Run: `uv run pytest tests/unit_tests/test_harness_contracts.py::test_agent_docs_describe_llms_gateway_boundary -q`

Expected: FAIL because current docs still describe `models`。

- [ ] **Step 3: 更新模块架构文档**

Write these exact boundary statements:

```text
contracts：跨 State、Node、Graph、LLM 实现共享的中立类型，不依赖实现层。
llms：实现 LLMGateway、provider 配置、model-family 客户端和统一响应，不知道业务 State/Prompt/Graph。
messages：构造可复用 LangChain 消息片段，不创建或调用模型。
nodes：只依赖 contracts 中的 LLMGateway；Prompt、Parser 和 Observability 是允许依赖。
graphs：作为组合根，把具体 LangChainLLMGateway 注入 Node Builder。
```

Update all module trees and links from `models` to `contracts`/`llms`/`messages`。Remove the obsolete Models architecture link from `src/agent/README.md` and add the three new architecture links。

- [ ] **Step 4: 记录正式决策**

Append `D017 - Agent 模型层升级为 LLM Gateway` to `docs/DECISIONS.md` with:

```text
决策：删除 agent.app.models；由 agent.app.contracts.llm 定义中立 Gateway 契约，agent.app.llms 提供 LangChain 实现，Graph Builder 显式注入，Node 不直接依赖实现层。
原因：统一供应商差异和响应结构，修复实际模型与审计模型可能不一致，并支持 Fake Gateway、A/B Graph 和后续供应商扩展。
影响：新增 contracts、llms、llms/families、messages；后端/API/数据库和图名不变；不保留旧导入兼容层。
```

- [ ] **Step 5: 更新进度记录**

Set `PROGRESS.md` 最后更新为 `2026-07-10`。Add a verification entry describing directory migration、Gateway injection、model identity fix、test commands and the fact that no real model call was run。Keep current active feature as none and do not change `docs/FEATURES.md`。

- [ ] **Step 6: 运行文档检查**

Run: `uv run pytest tests/unit_tests/test_harness_contracts.py -q && make docs-check`

Expected: tests PASS and output contains `docs-check passed`。

---

### Task 8: 完整验证和最终审计

**Files:**

- Modify only if verification finds a scoped defect in files already listed above。
- Do not expand into Graph use-case splitting or custom message protocols。

**Interfaces:**

- Verifies all acceptance criteria from the confirmed design spec。

- [ ] **Step 1: 运行单元测试**

Run: `uv run pytest tests/unit_tests -q`

Expected: exit 0, no failures。

- [ ] **Step 2: 运行集成测试**

Run: `uv run pytest tests/integration_tests -q`

Expected: exit 0, no failures。

- [ ] **Step 3: 运行静态检查**

Run: `uv run ruff check src/agent backend tests scripts`

Expected: exit 0。

- [ ] **Step 4: 验证文档和 LangGraph 配置**

Run: `make docs-check && uv run langgraph validate`

Expected: `docs-check passed` and `2 graphs found`。

- [ ] **Step 5: 运行仓库完整门禁**

Run: `make check`

Expected: unit tests、docs-check、LangGraph validate、frontend build all exit 0。

- [ ] **Step 6: 审计目录和依赖**

Run:

```bash
test ! -d src/agent/app/models
! rg -n "agent\.app\.(models|llms)" src/agent/app/nodes
rg -n "model_ref|model_calls" src/agent/app/nodes src/agent/app/llms/gateway.py
```

Expected:

- first command exit 0；
- Node files contain no concrete `models`/`llms` import；
- model identity is read from `LLMResponse.model_ref` when writing State/model_calls。

- [ ] **Step 7: 最终文档核对**

Confirm `PROGRESS.md` records every command actually run and any remaining gap。Do not claim real-provider compatibility beyond mocked tests because no credentialed external call is authorized。
