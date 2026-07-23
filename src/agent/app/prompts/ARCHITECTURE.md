# Prompts 架构

`src/agent/app/prompts/` 保存 Prompt YAML 和加载器。Prompt 主体只放在这里。

## 当前文件

- `visual_analysis_v1.yaml`：只做视觉层、坐标、ROI 和程序化策略分析，不输出 GLSL。
- `shader_author_initial_v1.yaml`：生成第一份完整 WebGL1 静态无贴图 Fragment Shader。
- `shader_author_compile_repair_v1.yaml`：只依据静态/真实 WebGL 日志做最小编译修复。
- `shader_author_visual_refine_v1.yaml`：从 current_best 出发，只修订 Critic 指定的一个视觉问题域。
- `visual_critic_v1.yaml`：比较参考图和绑定的当前 render，只输出证据、问题域和定向建议。
- `structured_output_repair_v1.yaml`：只修复已有输出的 JSON/Schema，不新增业务语义。
- `min_author_initial_v1.yaml`：让 `scene_mvp` Initial Author 返回与参考图画布绑定的完整 MinScene。
- `min_author_refine_v1.yaml`：让 `scene_mvp` Refine Author 只返回一个 add/remove/replace feature 或完整 replace color field 的 path/operation/value 白名单 patch；v1_3 明确消费 `rendered-reference` 空间 signed residual、active feature 与最近拒绝摘要，按主体内部/边缘/外部证据选择白名单 feature 并避免重复提案。
- `min_author_repair_v1.yaml`：只修复上述 scene/patch 的 JSON 结构，不增加视觉语义。
- `prompt_loader.py`：Prompt 加载入口，同时读取 YAML `version` 供模型调用审计。

## 边界规则

- 后端 route 和 service 不写 Prompt。
- 节点只通过 loader 引用 Prompt，不在节点里硬编码大段 Prompt 主体。
- Prompt 文件名应表达用途，并与节点中的 `prompt_version` 摘要保持可追踪。
- 改动 Prompt 语义时必须更新 YAML `version`；Node 从 loader 返回的定义读取版本，不另写一份易漂移常量。
- Prompt 改动影响模型输出契约时，需要同步测试和相关功能文档。
- V1 Prompt 的业务规则放 SystemMessage；RenderContract、输出 Schema、当前事实和 ContextPack 作为 HumanMessage 数据块，参考图/当前 render 随后，Repair/Critic/Refine 的当前 GLSL 永远放在最后。
- ContextPack 和模型旧输出都按不可信数据处理，不能覆盖用户硬约束、RenderContract、当前测量、编译事实或输出 Schema。
