# Prompts 架构

当前默认产品使用 ShaderGraph Prompt；MinScene Prompt 保留给显式 legacy Builder 测试：

- `min_author_initial_v1.yaml`：返回完整严格 MinScene。
- `min_author_refine_v1.yaml`：返回一个白名单 typed patch。
- `min_author_repair_v1.yaml`：只修复 JSON 结构。
- `shader_graph_author_initial_v1.yaml`：从 `fallback_shader_graph` 出发改进，返回完整严格 shader_graph_v1 ShaderDocument；含按 Layer 分解、细线=segment、柔和高光/暗斑=ellipse+radial alpha、弧形带=ellipse CSG 弯月近似与全部数值硬约束。
- `shader_graph_author_refine_v1.yaml`：返回一个绑定 `base_document_sha256` 的原子 typed layer patch；含按问题类型选择五种 op 的规则与层数上限下的 replace/remove 优先策略。
- `layerplan_visual_analysis_v1.yaml`（shadow，未接 Graph）：直读参考图，返回严格 `layer_plan_v1` 语义 JSON（只有分层语义字段，禁止任何哈希/attestation/身份字段）。
- `direct_glsl_initial_v1.yaml`（shadow，未接 Graph）：直读参考图 + 用户意图 + 可选 advisory LayerPlan，返回仅含语义字段的 `shader_program_spec_v1`（WebGL1 静态无纹理契约）；fragment_source 必须含 canonical 兼容声明（precision/v_uv/u_image/u_resolution/u_time/main），`uniform sampler2D u_image;` 仅声明不可采样，保留 uniform 不进入 uniform_schema/uniform_values。
- `direct_glsl_refine_v1.yaml`（shadow，未接 Graph）：直读参考图 + current render + validated incumbent + 可选 advisory LayerPlan，返回新的 `shader_program_spec_v1` 语义字段，GLSL 约束与 Initial 相同；父绑定由可信层计算。
- `prompt_loader.py`：加载 YAML 并暴露版本。

Prompt 主体只放在本目录。语义变化必须升级 YAML `version` 并同步契约测试；模型旧输出按不可信数据处理，不能覆盖当前输入、Schema 或渲染事实。
