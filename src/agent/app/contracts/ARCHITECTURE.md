# Contracts 架构

`src/agent/app/contracts/` 保存跨 State、Node、Graph 和具体适配器共享的中立契约。

## 当前文件

- `llm.py`：定义 `LLMGateway`、`LLMCallOptions`、`LLMResponse`、token usage、thinking、`text | json_object` 输出格式和统一错误。
- `png_to_shader_min.py`：定义 `scene_mvp` Refine Author 的单个 typed patch 联合类型；只允许 add/remove/replace feature 与完整 replace color field 四种固定 `path/operation/value` 组合，并适配到领域 scene patch。`summarize_min_author_patch()` 以规范 typed JSON 生成 SHA-256，只公开 operation、feature id/type 和指纹，不泄露完整 Patch value。
- `png_to_shader_min_replay.py`：定义私有 Patch replay bundle v1 的 schema 常量（bundle/step/patch）、`private/replay/` 目录约定、zero-padded step 目录名、排版无关的 canonical JSON SHA-256 与 bytes SHA-256 工具，以及允许进入公开 manifest 的 `build_bundle_summary()` hash 级摘要（`durability_status=local_ignored`）。`decode_verified_replay_json()` 是唯一的 replay JSON 读回入口：fail-closed 精确校验引用 path/sha256/size_bytes、预期 `private/replay/` 路径、JSON object、schema_version 与 refine_count，拒绝篡改与路径注入。完整 replay 内容永不进入公开账本。
- `png_to_shader_v1.py`：定义 Analyst、三模式 Author、Critic 的严格 Pydantic 输出契约，以及候选/渲染绑定、`model_calls` 审计和 Candidate provenance。

## 边界规则

- Contracts 不依赖 `llms`、`nodes`、`graphs`、`services` 或后端。
- Node 只通过 `LLMGateway` 使用模型能力。
- `response_format` 是供应商中立调用语义；业务 Node 不直接拼接 DashScope/OpenAI 的请求字典。
- 共享运行类型先进入 Contracts，不放在具体 model-family 适配器中。
- 模型角色契约默认 `extra=forbid`、不可变并带字段上限；角色越权、问题域、Prompt 版本和候选绑定由 Parser 继续做上下文校验。
- `ShaderAuthorResult.glsl` 在契约层只检查“完整源码形状”，不调用 Validator 或 Renderer；真实 WebGL1/无贴图事实仍由 `src/shaderforge/` 判断。
- 契约变化影响 Node、Graph 或 Service 时，必须同步测试和对应架构文档。
