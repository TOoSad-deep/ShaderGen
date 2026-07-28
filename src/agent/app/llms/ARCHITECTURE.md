# LLMs 架构

`src/agent/app/llms/` 是 `LLMGateway` 的具体实现层，封装 LangChain 客户端、provider 配置、model-family 路由和统一响应。

## 当前文件

- `gateway.py`：实现 `LangChainLLMGateway`，统一调用、耗时、模型身份、reasoning、usage 和错误。
- `client_factory.py`：解析 `provider:model`、按真实模型名选择 model family，并把请求 ref、解析后的 provider 和配置模型名绑定到客户端。
- `provider_config.py`：维护 provider 的 API key、base URL、默认地址、统一模型 HTTP timeout，并把中立输出格式映射为 OpenAI-compatible `response_format`。
- `families/qwen.py`：处理 Qwen thinking 和 `reasoning_content`。
- `families/glm.py`、`deepseek.py`、`openai.py`：创建对应 model-family 客户端。
- `families/kimi.py`：Kimi Code 端点仅允许 temperature=1，family 层忽略调用方温度并固定为 1；thinking effort 通过 `SHADER_GEN_KIMI_REASONING_EFFORT`（low/high/max，默认 low）下发 `reasoning_effort`。

## 边界规则

- provider 只表示凭据和 base URL 来源；Qwen、GLM、DeepSeek、OpenAI、Kimi 表示 model family。
- LLMs 实现 `agent.app.contracts.llm`，不依赖业务 State、Prompt、Node、Graph 或后端。
- Gateway 优先从响应 `model_name` / `model` 元数据构造 `LLMResponse.model_ref`；供应商未返回时才使用解析后的 provider 和配置模型名，并以 `model_identity_source` 区分 `response_metadata` 与 `configured_fallback`。
- `LLMResponse.requested_model_ref` 保留调用方请求值，不能替代实际 `model_ref`；Node 的业务状态和 Candidate provenance 以实际值为准，同时保留请求值供审计。
- `LLMResponse.effective_identity` 记录 Gateway 可信的**实际生效**调用身份：`provider` + 实际 `model_ref` + `model_identity_source` + `EffectiveSamplingParams`（family 工厂真实下发的 temperature/thinking/reasoning_effort/response_format/max_output_tokens）。`client_factory.resolve_effective_sampling` 按 family 行为给出事实值——kimi 记录 `temperature=1` 与 `reasoning_effort`（thinking 请求被端点忽略，记 None），qwen 记录规范化 thinking，其余 family 的 thinking 记 None；绝不回写 `LLMCallOptions` 请求假值。当前结构化 Author 在 `effective_identity` 缺失时必须 fail-closed。
- model-family 私有响应字段必须在 Gateway 边界规范化，Node 不读取供应商字段。
- `LLMCallOptions.response_format=json_object` 由各 OpenAI-compatible family 映射为请求级 JSON mode；结构化角色必须同时关闭不兼容的 thinking，普通文本节点继续使用 `text`。
- Gateway 不输出 API key、base64 图片、完整原始响应或 reasoning 到错误字符串。
- 所有 OpenAI-compatible family 的单次 HTTP 请求 timeout 读取 `shaderforge/config/runtime_timeouts.yaml` 的 `llm.request_seconds`；只接受正有限秒数，不能配置为无限等待。当前 3600 秒边界小于产品单 engine attempt 的 7200 秒，为同一次 attempt 的解析、repair、Renderer 和收尾保留空间。
- 新增 provider 时扩展 `provider_config.py`；新增 model family 时扩展 `families/` 和 `client_factory.py`。
