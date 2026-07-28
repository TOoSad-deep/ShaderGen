# 当前交接

最后更新：2026-07-28

> 本文件不是逐会话追加日志，只保留下一次开发需要知道的事实。

## 当前状态

- 当前唯一产品功能是 `scene_mvp`，唯一 `active` 功能是 F09。
- 无显式 engine policy 时默认执行 `direct_glsl_layerplan_v1`；direct 失败创建隔离的 `shader_graph_v1` fallback attempt。
- `langgraph.json` 只注册 `png_to_shader_min`。Node Lab、Memory/checkpoint 和质量实验设施都不在默认产品链路。

## 当前 active 功能

F09：贯通“上传 PNG → 生成 Shader → WebGL 渲染 → 查看 GLSL、Render、指标和失败信息”，以实际需求正确、失败可观察、相关 happy path 可用作为当前完成判断。

## 下一步

- 按用户提出的具体使用问题继续迭代，不从历史缺口自动扩展范围。
- 修改后运行相关聚焦测试；跨组件行为再验证一条代表性 happy path。

## 未解决缺口

- direct GLSL 可能因模型输出不满足 Parser/Renderer 契约而进入 fallback，按真实案例修复。
- Backend 仍阻塞执行；前端停止等待不等于服务端取消。
- 进度状态是单进程内存数据，进程重启后丢失。

## 当前验证基线

- 2026-07-27 最近一次完整里程碑验证通过，细节已归档。
- 2026-07-28 文档/process 清理与默认上下文瘦身通过 `make docs-check` 和 `git diff --check`；未运行全量测试或模型实验。

历史记录统一从 [docs/archive/2026-07/README.md](docs/archive/2026-07/README.md) 精确查阅，不作为当前任务来源。
