# 当前交接

最后更新：2026-07-28

> 本文件不是逐会话追加日志，只保留下一次开发需要知道的事实。

## 当前状态

- 当前唯一产品功能是 `scene_mvp`，唯一 `active` 功能是 F09。
- 无显式 engine policy 时默认执行 `direct_glsl_layerplan_v1`；单次 attempt 的结构修复仍失败后，创建一个隔离的 fresh direct attempt 重试。两次均失败则返回 `direct_attempts_failed`，不自动降级到 ShaderGraph。
- 默认 direct 已贯通 `LayerPlanV1 -> LayeredShaderSpecV1 -> 确定性
  ShaderProgramSpecV1`；Initial 按 Layer 生成，Refine 每轮只允许替换一个
  Layer，最终仍按整图 strict loss 验收。
- 模型、Renderer、engine attempt 和前端等待统一由 `src/shaderforge/config/runtime_timeouts.yaml` 提供有界默认值；Python 与 Vite 严格校验该文件。
- `langgraph.json` 只注册 `png_to_shader_min`。Node Lab、Memory/checkpoint 和质量实验设施都不在默认产品链路。

## 当前 active 功能

F09：贯通“上传 PNG → 生成 Shader → WebGL 渲染 → 查看 GLSL、Render、指标和失败信息”，以实际需求正确、失败可观察、相关 happy path 可用作为当前完成判断。

## 下一步

- 按用户提出的具体使用问题继续迭代，不从历史缺口自动扩展范围。
- 修改后运行相关聚焦测试；跨组件行为再验证一条代表性 happy path。

## 未解决缺口

- 真实并行运行已证明 Layered 链路可成功生成和完成两轮单 Layer Refine；复杂
  ripples 首次 attempt 成功，bubble fresh retry 成功。heart 仍会受到模型
  transient 与 `author_output_invalid` 影响；Parser 现已把稳定领域错误类别传给
  Layered 专用 repair 和私有诊断，下一次真实失败可直接按类别处理。
- Backend 仍阻塞执行；前端停止等待不等于服务端取消。
- 进度状态是单进程内存数据，进程重启后丢失。

## 当前验证基线

- 2026-07-28 D102 runtime timeout 配置在 `origin/main` 通过 `make check`、75 个相关聚焦测试、Ruff、mypy 与 `git diff --check`；未调用真实模型。
- 2026-07-28 文档/process 清理与默认上下文瘦身通过 `make docs-check`、`git diff --check` 和 Markdown 链接检查；未运行模型实验。
- 2026-07-28 Layered direct 改造与真实运行修复通过 86 个受影响聚焦测试、
  Ruff、`git diff --check` 和一条真实 Chromium/WebGL1 happy path。并行真实
  PNG 运行中 bubble、ripples 成功；修复了 `/ 0.x` 被误判为字面量除零、
  Layered Initial 结构修复缺少固定 Plan/Canvas 约束、失败诊断丢失等问题。

历史记录统一从 [docs/archive/2026-07/README.md](docs/archive/2026-07/README.md) 精确查阅，不作为当前任务来源。
