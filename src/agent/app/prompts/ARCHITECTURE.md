# Prompts 架构

当前默认 direct engine 使用 LayerPlan/direct GLSL Prompt。ShaderGraph Prompt 只在服务端明确选择 ShaderGraph engine 时使用，不参与 direct 失败重试。

- `shader_graph_author_initial_v1.yaml`：从 `fallback_shader_graph` 出发改进，返回完整严格 shader_graph_v1 ShaderDocument；含按 Layer 分解、细线=segment、柔和高光/暗斑=ellipse+radial alpha、弧形带=ellipse CSG 弯月近似与全部数值硬约束。
- `shader_graph_author_refine_v1.yaml`：返回一个绑定 `base_document_sha256` 的原子 typed layer patch；含按问题类型选择五种 op 的规则与层数上限下的 replace/remove 优先策略。
- `layerplan_visual_analysis_v1.yaml`（direct 与历史 shadow 共用，未接 Graph）：直读参考图，返回严格 `layer_plan_v1` 语义 JSON（只有分层语义字段，禁止任何哈希/attestation/身份字段）；region 与 canonical `NormalizedRegion` 一致，固定为左下原点、x 向右、y 向上，参考图行坐标不得直接充当 region.y。
- `direct_layered_initial_v1.yaml`（默认 direct）：直读参考图、用户意图和
  canonical LayerPlan，逐层返回 `layered_shader_spec_v1` 的模型语义字段。
  每层只提供固定函数体和本层 uniforms，不得输出 `main` 或全局声明。
- `direct_layered_refine_v1.yaml`（默认 direct）：直读参考图、current render、
  validated incumbent、metric 和 residual，只返回一个 `layer_patch_v1`，
  不得重写非目标 Layer。
- `direct_layered_repair_v1.yaml`：只修复 Layered Initial/Patch 的 JSON 结构；
  Initial 的动态 Schema 绑定本轮 canvas 和逐层 `id/role/z_index`，修复调用
  不重新猜测可信 Layer 身份，也不增加视觉语义。
- `direct_glsl_initial_v2.yaml` / `direct_glsl_refine_v2.yaml`：仅供休眠的
  historical shadow Harness 兼容，返回整份 `shader_program_spec_v1`，不再
  进入默认产品 direct runner。
- `prompt_loader.py`：加载 YAML 并暴露版本。

`min_author_*` 只保留给 legacy Builder 和共享的 JSON repair；普通产品改动不读取。

Prompt 主体只放在本目录。语义变化必须升级 YAML `version` 并同步契约测试；模型旧输出按不可信数据处理，不能覆盖当前输入、Schema 或渲染事实。
