# layerplan_glsl_shadow 节点包

LayerPlan + 直接 GLSL 的三个独立有界 Author helper。它们不注册或修改任何 LangGraph，但当前由默认 direct engine 与休眠的 shadow A/B Harness 共用。当前调用关系以本文件、Service 架构和代码为准。

## 组成

- `authors.py`：三个 Author helper 与结果契约。
  - `run_visual_analysis_author`：直接读取参考图（可选 perception 观测与用户意图），输出可信装配后的 canonical `LayerPlanV1`（永久 advisory，不参与 scorer/acceptance）。
  - `run_initial_glsl_author`：直接读取参考图 + 用户意图 + 可选 LayerPlan，输出 canonical `ShaderProgramSpecV1`（role=initial，无父绑定）。
  - `run_refine_glsl_author`：直接读取参考图 + current render + metric/residual + `ValidatedIncumbent` + 可选 LayerPlan，输出 canonical `ShaderProgramSpecV1`（role=refine），并把 `parent_spec_sha256` 绑定为 incumbent 的 canonical `spec_sha256`（不来自模型输出）。

## 边界

- **唯一 canonical 契约是 `shaderforge.program_spec`**：`agent.app.contracts.layerplan_glsl_shadow` 只是模型 JSON schema + 薄 adapter，不定义第二套 LayerPlan/ProgramSpec 类型、规范化或哈希语义；语义校验、哈希重算与 author/input 身份绑定全部委托 `build_layer_plan`/`build_program_spec`/`build_author_identity`。模型输出只含语义字段，自带 attestation/哈希/author_identity 字段即 fail-closed 拒绝（`untrusted_attestation_or_hash_field`），Author 不签发任何 attestation。
- GLSL 输出契约与真实 canonical WebGL1 Renderer 完全一致：fragment_source 必须含 canonical 兼容声明（precision mediump float、varying vec2 v_uv、uniform sampler2D u_image、uniform vec2 u_resolution、uniform float u_time、void main()），满足 `validate_shader` 全量静态规则；`uniform sampler2D u_image;` 是**仅声明、不可采样**的兼容占位——禁止 texture2D/textureCube/texture/texelFetch 调用、扩展与任何其他 sampler 声明（`glsl_renderer_contract_violation`）；保留 uniform（u_image/u_resolution/u_time）由 Renderer 自动上传，不进入 uniform_schema/uniform_values/tunable_manifest。静态校验直接使用 canonical `validate_program_spec_safety`/`validate_shader`，无任何豁免或绕过。
- Author 结果返回装配好的 canonical Spec/Plan：`author_identity` 绑定真实参考图/指令哈希、content type、角色输入上下文、model_ref、prompt_version 与 Gateway effective 采样身份；Arm B 额外绑定 `plan_sha256`，Refine 绑定 `parent_spec_sha256`。若走结构修复，还用 `repair_context_sha256` 绑定 repair Prompt、首轮输出/错误、Schema 以及首轮与第二次实际调用身份。
- 复用 `agent.app.nodes.png_to_shader_min.model_author.invoke_min_author` 的有界调用（最多语义一次 + 结构修复一次）与统一 LLMGateway；请求为 temperature=0、`json_object`，但实际生效采样以 family/Gateway 记录为准（例如 kimi 强制 temperature=1），缺可信 effective identity 时 fail-closed。
- Prompt 主体在 `agent.app.prompts`：`layerplan_visual_analysis_v1.yaml`、`direct_glsl_initial_v1.yaml`、`direct_glsl_refine_v1.yaml`；结构修复复用 `min_author_repair_v1`。
- 所有错误收敛为结果对象上的 `error_code`，不冒泡未分类异常；预算耗尽返回 `llm_budget_exhausted` 且不调用模型。
