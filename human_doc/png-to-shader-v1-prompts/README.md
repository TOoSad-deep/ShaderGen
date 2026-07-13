# PNG to Shader V1 Prompt 草案

这些 YAML 是实现规格，不会被当前运行时自动加载。进入 V1 实现时，将模型 Prompt 移入 `src/agent/app/prompts/`，将 `orchestrator_policy_v1.yaml` 移入合适的配置目录，并同步 Parser、Node、测试和 Prompt 版本。

文件：

- `orchestrator_policy_v1.yaml`：确定性主控策略，不发送给模型；
- `visual_analysis_v1.yaml`：VisualAnalysisAgent；
- `shader_author_initial_v1.yaml`：ShaderAuthorAgent 初稿；
- `shader_author_compile_repair_v1.yaml`：ShaderAuthorAgent 编译修复；
- `shader_author_visual_refine_v1.yaml`：ShaderAuthorAgent 视觉修订；
- `visual_critic_v1.yaml`：VisualCriticAgent。

所有坐标使用 Shader UV：左下为 `(0, 0)`，右上为 `(1, 1)`。
