# LLMs 架构

`src/agent/app/llms/` 是 `LLMGateway` 的具体实现层，封装 LangChain 客户端、provider 配置、model-family 路由和统一响应。

## 当前文件

- `gateway.py`：实现 `LangChainLLMGateway`，统一调用、耗时、模型身份、reasoning、usage 和错误。
- `client_factory.py`：解析 `provider:model` 并按真实模型名选择 model family。
- `provider_config.py`：维护 provider 的 API key、base URL 和默认地址。
- `families/qwen.py`：处理 Qwen thinking 和 `reasoning_content`。
- `families/glm.py`、`deepseek.py`、`openai.py`：创建对应 model-family 客户端。

## 边界规则

- provider 只表示凭据和 base URL 来源；Qwen、GLM、DeepSeek、OpenAI 表示 model family。
- LLMs 实现 `agent.app.contracts.llm`，不依赖业务 State、Prompt、Node、Graph 或后端。
- Gateway 返回的 `LLMResponse.model_ref` 是本次真实调用和审计记录的模型身份。
- model-family 私有响应字段必须在 Gateway 边界规范化，Node 不读取供应商字段。
- Gateway 不输出 API key、base64 图片、完整原始响应或 reasoning 到错误字符串。
- 新增 provider 时扩展 `provider_config.py`；新增 model family 时扩展 `families/` 和 `client_factory.py`。
