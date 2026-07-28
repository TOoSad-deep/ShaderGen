# Frontend

前端提供 `scene_mvp` 产品页和独立 Node Lab 工作台。产品页不选择 engine，只展示 Backend 实际返回的 direct 或 ShaderGraph fallback 结果。

## 产品页契约

- `src/api/` 统一处理生成、进度、运行中 Render、Artifact URL 和错误。
- `App.tsx` 负责上传、质量档位、轮询和结果展示。
- `src/runStages.ts` 只根据服务端事实推导运行阶段、失败位置、预算和来源；不猜测百分比、执行中节点或最终 provenance。
- 进度轮询保持 single-flight、事件按 seq 去重；停止等待不等于取消服务端 run。
- direct 成功时 `scene=null`，页面展示 Direct Program；fallback 返回 `shader_graph_v1` 时才展示 Layer inspector。
- 缺少 engine discriminator 的旧响应仅做兼容展示，不根据 `scene` 猜测 engine。
- 前端不发送 `generation_mode`，也不调用休眠的项目 Memory API。

## Node Lab

`/lab` 连接独立 Node Lab 服务，不复用产品 Backend。默认空 Application 只显示接入引导；显式设置 `NODELAB_APPLICATION_FACTORY=agent.app.services.node_lab:create_application` 后可调试当前 ShaderGraph Node。

## 配置与验证

- `VITE_API_BASE_URL`：产品 Backend。
- `VITE_NODE_LAB_API_BASE_URL`：独立 Node Lab。
- `VITE_GENERATION_REQUEST_TIMEOUT_MS`：可选的生成等待覆盖值。
- 默认 POST、进度 GET 和终态观察窗口统一来自 `src/shaderforge/config/runtime_timeouts.yaml` 的 `frontend` 段；修改后必须重启 Vite 或重新构建。

`VITE_*` 都会进入浏览器产物，不能包含秘密；完整示例见 `.env.example`。

测试按根 `AGENTS.md` 选择。组件或视图模型改动运行相关 Vitest；跨前后端行为再运行一条 `e2e:scene-mvp` happy path。
