# Contracts 架构

`src/agent/app/contracts/` 保存跨 State、Node、Graph 和具体适配器共享的中立契约。

## 当前文件

- `llm.py`：定义 `LLMGateway`、`LLMCallOptions`、`LLMResponse`、token usage、thinking、`text | json_object` 输出格式和统一错误；`EffectiveSamplingParams`/`EffectiveCallIdentity` 描述一次调用实际生效的采样参数与 provider/model/identity source（由 Gateway 按 family 事实填写，非请求值），`LLMResponse.effective_identity` 缺省 None，需要可信身份的调用方必须 fail-closed。
- `png_to_shader_min.py`：定义 `scene_mvp` Refine Author 的单个 typed patch 联合类型；只允许 add/remove/replace feature 与完整 replace color field 四种固定 `path/operation/value` 组合，并适配到领域 scene patch。`summarize_min_author_patch()` 以规范 typed JSON 生成 SHA-256，只公开 operation、feature id/type 和指纹，不泄露完整 Patch value。
- `shader_graph_author.py`：定义 ShaderGraph Refine Author 的单个 typed layer patch 判别联合（`operation` 判别），只允许 add_layer_bundle/remove_layer/replace_layer_bundle/reorder_layer/replace_canvas_background 五种原子 op；每个 patch 必须携带 `base_document_sha256`，应用时先比对 `document_sha256`，再以完整重建触发全图校验（id 唯一、层级/primitive/CSG 预算、opaque 背景），失败只抛稳定错误码。DSL shape 为内联树、无 id 引用，不可达节点在结构上不可表示。`summarize_shader_graph_author_patch()` 只公开 operation、layer id、节点类型集合、base hash 前缀和指纹。
- `png_to_shader_min_replay.py`：定义私有 Patch replay bundle v1 的 schema 常量（bundle/step/patch）、`private/replay/` 目录约定、zero-padded step 目录名、排版无关的 canonical JSON SHA-256 与 bytes SHA-256 工具，以及允许进入公开 manifest 的 `build_bundle_summary()` hash 级摘要（`durability_status=local_ignored`）。`decode_verified_replay_json()` 是唯一的 replay JSON 读回入口：fail-closed 精确校验引用 path/sha256/size_bytes、预期 `private/replay/` 路径、JSON object、schema_version 与 refine_count，拒绝篡改与路径注入。该契约当前只由 legacy MinScene Builder 使用；完整 replay 永不进入公开账本，默认 ShaderGraph 产品也尚未迁移 typed layer patch replay。
- `layerplan_glsl_shadow.py`：LayerPlan + 直接 GLSL Author shadow 的模型 JSON schema 与薄 adapter。**唯一 canonical 契约是 `shaderforge.program_spec`**：本模块不定义任何 LayerPlan/ProgramSpec 数据结构、规范化规则或哈希语义，`LayerPlanV1`/`ShaderProgramSpecV1`/`AuthorIdentity` 等名字只是 canonical 类型的再导出。保留的只有：发给模型的严格 JSON Schema（形状与 canonical 模型输出一致：uniform_schema 为 `{u_ 名: 声明}` 映射、float 分量用标量、tunable path 即 uniform 名）、fail-closed 严格 JSON 预检（字符上限、重复 key、非有限数、模型自带 attestation/哈希/author_identity 字段，`untrusted_attestation_or_hash_field`）、画布一致性检查与 shadow GLSL 契约检查（`glsl_renderer_contract_violation`）：fragment_source 必须满足 canonical `validate_shader` 全量静态规则（precision mediump float、varying vec2 v_uv、uniform sampler2D u_image、uniform vec2 u_resolution、uniform float u_time、void main() 声明齐备，禁止纹理采样调用与扩展），且只允许保留的兼容 sampler 声明 `uniform sampler2D u_image;`——**仅声明、不可采样**，任何其他 sampler 声明一律拒绝；保留 uniform（u_image/u_resolution/u_time）由 Renderer 自动上传，不进入 ProgramSpec 的 uniform_schema/uniform_values/tunable_manifest。`parse_*_semantics()` 把语义校验完全委托 `build_layer_plan`/`build_program_spec`（probe 身份用完即弃）并返回未装配 semantics mapping；`assemble_*()` 用真实 author/input 身份（参考图/指令/父 Spec 哈希）调用 canonical build 完成装配，`ValidatedIncumbent` 只接受 canonical `ShaderProgramSpecV1`。此外本模块定义角色输入上下文绑定：`initial_input_context_sha256` 绑定 reference content_type、canvas 与注入的 LayerPlan；`refine_input_context_sha256` 绑定 reference/current_render content_type、current_render 内容哈希、canonical 评估上下文（mae/loss/metrics/residual 哈希 + metric version + `SHADOW_METRIC_PREPROCESS` 预处理事实）与 LayerPlan；两者都以 `input_context_sha256` 进入 canonical `AuthorIdentity` 并参与 `spec_sha256`。结构修复 provenance 由调用层计算为 `repair_context_sha256`，同样进入 canonical identity；篡改任一输入或修复上下文即哈希失配。

## 边界规则

- Contracts 不依赖 `llms`、`nodes`、`graphs`、`services` 或后端。
- Node 只通过 `LLMGateway` 使用模型能力。
- `response_format` 是供应商中立调用语义；业务 Node 不直接拼接 DashScope/OpenAI 的请求字典。
- 共享运行类型先进入 Contracts，不放在具体 model-family 适配器中。
- 模型角色契约默认 `extra=forbid`、不可变并带字段上限；Prompt 版本和 Scene/Patch 绑定由 Parser 继续做上下文校验。
- 契约变化影响 Node、Graph 或 Service 时，必须同步测试和对应架构文档。
