# Prompts 架构

`src/agent/app/prompts/` 保存 Prompt YAML 和加载器。Prompt 主体只放在这里。

## 当前文件

- `visual_analysis_v1.yaml`：只做视觉层、坐标、ROI 和程序化策略分析，不输出 GLSL。
- `analyze_visual_layers_v2.yaml`：V2.1 VisualInterpretation 专用 Prompt，只输出视觉推断，不回写 target 身份、尺寸、bbox、hard fact 或 GLSL。
- `shader_author_initial_v1.yaml`：生成第一份完整 WebGL1 静态无贴图 Fragment Shader。
- `shader_author_compile_repair_v1.yaml`：只依据静态/真实 WebGL 日志做最小编译修复。
- `shader_author_visual_refine_v1.yaml`：从 current_best 出发，只修订 Critic 指定的一个视觉问题域。
- `visual_critic_v1.yaml`：比较参考图和绑定的当前 render，只输出证据、问题域和定向建议。
- `structured_output_repair_v1.yaml`：只修复已有输出的 JSON/Schema，不新增业务语义。
- `prompt_loader.py`：Prompt 加载入口，同时读取 YAML `version` 供模型调用审计。

## 边界规则

- 后端 route 和 service 不写 Prompt。
- 节点只通过 loader 引用 Prompt，不在节点里硬编码大段 Prompt 主体。
- Prompt 文件名应表达用途，并与节点中的 `prompt_version` 摘要保持可追踪。
- 改动 Prompt 语义时必须更新 YAML `version`；Node 从 loader 返回的定义读取版本，不另写一份易漂移常量。
- Prompt 改动影响模型输出契约时，需要同步测试和相关功能文档。
- V1 Prompt 的业务规则放 SystemMessage；RenderContract、输出 Schema、当前事实和 ContextPack 作为 HumanMessage 数据块，参考图/当前 render 随后，Repair/Critic/Refine 的当前 GLSL 永远放在最后。
- ContextPack 和模型旧输出都按不可信数据处理，不能覆盖用户硬约束、RenderContract、当前测量、编译事实或输出 Schema。

## V2.1 VisualInterpretation 边界与审计

- V2 Prompt 的 JSON 必须精确匹配 `visual_interpretation_v2_1`；Prompt/Model 审计不属于该 Schema，不得塞入模型输出。九类 `layer_hypotheses` 之外还必须按共享十项 taxonomy 输出 `required_layer_assessments` 闭集；每项显式为 `required | not_required | unknown` 并绑定 confidence、model provenance 和授权 evidence，`glow` 只出现在该闭集而不扩张九类 role。
- 调用方必须提供非空、版本化的 `allowed_primitive_ids` 和 `allowed_template_ids`；模型只能从目录中选择，不得创造可执行 ID。
- 模型只能复制调用方授权的 ArtifactRefV2；未授权时 `evidence_refs` 必须为空。Parser/Builder 仍需独立拒绝未授权引用，Prompt 不是信任边界。
- 后续 `analyze_visual_layers_v2` 节点每次调用必须在 VisualInterpretation Artifact 之外持久化 Prompt name/version、实际 model ref、输入 Artifact refs、原始响应 SHA-256、attempt/repair 计数、Parser 状态和输出 Artifact ref；原始响应不得写入日志。
- `analyze_visual_layers_v2.yaml` 当前审计版本为 `analyze_visual_layers_v2_2`。任何改变视觉角色、required-layer 闭集、允许/禁止字段、候选目录、evidence 绑定或输出规则的语义改动，必须同步提升 YAML `version` 并更新契约测试。
