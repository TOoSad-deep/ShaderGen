# Contracts 架构

`src/agent/app/contracts/` 保存跨 State、Node、Graph 和具体适配器共享的中立契约。

## 当前文件

- `llm.py`：定义 `LLMGateway`、`LLMCallOptions`、`LLMResponse`、token usage、thinking、`text | json_object` 输出格式和统一错误。
- `png_to_shader_min.py`：定义 `scene_mvp` Refine Author 的单个 typed patch 联合类型；只允许 add/remove/replace feature 与完整 replace color field 四种固定 `path/operation/value` 组合，并适配到领域 scene patch。`summarize_min_author_patch()` 以规范 typed JSON 生成 SHA-256，只公开 operation、feature id/type 和指纹，不泄露完整 Patch value。

## 边界规则

- Contracts 不依赖 `llms`、`nodes`、`graphs`、`services` 或后端。
- Node 只通过 `LLMGateway` 使用模型能力。
- `response_format` 是供应商中立调用语义；业务 Node 不直接拼接 DashScope/OpenAI 的请求字典。
- 共享运行类型先进入 Contracts，不放在具体 model-family 适配器中。
- 模型角色契约默认 `extra=forbid`、不可变并带字段上限；Prompt 版本和 Scene/Patch 绑定由 Parser 继续做上下文校验。
- 契约变化影响 Node、Graph 或 Service 时，必须同步测试和对应架构文档。
