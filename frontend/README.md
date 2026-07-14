# Frontend

前端负责用户输入、图片预览、Shader/WebGL 预览、生成结果展示和用户交互状态。前端不负责模型 Prompt 组装、Shader 搜索、评分、持久化或后端业务规则。

## 目录规范

- `src/api/`：后端 API 封装。组件不得直接拼接 `fetch` URL。
- `src/components/`：可复用 UI 和 WebGL 预览组件。组件只接收 props，不直接了解后端业务流程。
- `src/pages/`：页面级组合。页面可以管理页面状态和调用 `src/api/`。
- `src/styles/`：全局样式和页面样式。避免把大量内联样式写进组件。
- `public/`：静态资源。

## API 规则

- 所有后端调用集中在 `src/api/`。
- API base URL 通过 `VITE_API_BASE_URL` 配置，默认只用于本地开发。
- API 函数返回明确的 TypeScript 类型。
- 后端错误要转成用户能理解的错误消息；不要把原始异常对象直接展示给用户。
- 当前 `src/api/shader.ts` 封装 GLSL 生成和渲染评审。Generate 显式发送 generation mode、质量档位、补充约束和 project_id；legacy 评审请求继续上传原图、canvas 渲染图和 GLSL。
- `procedural_v1` 读取 run/current_best/score/stop/render 尺寸和白名单 Artifact URL；相对 URL 必须经 `resolveShaderApiUrl()` 解析，不在组件中手拼 API base。
- 生成失败兼容旧版 `detail: string` 和类型化 `detail: {message, code, run_id, stage, retryable, stop_reason}`；页面必须展示可用于后端检索的 Run ID 和失败阶段，不把服务端超时误报为客户端输入错误。
- Generate 使用 `AbortController` 支持“停止等待”和浏览器端兜底超时。它只中止客户端 HTTP 等待，不等价于服务端取消；自动等待上限可通过 `VITE_GENERATION_REQUEST_TIMEOUT_MS`（毫秒，最小 10000）覆盖。
- Generate/Review 都传递 `project_id` 并读取 `memory_status`；清除项目记忆只通过 `clearProjectMemory()`。
- `instruction` 只属于 `procedural_v1`；Legacy 模式必须禁用该输入并发送空值，禁止让用户填写后再被后端静默忽略。

## 组件规则

- 页面级状态留在页面或 `App`，可复用组件只处理自身交互和渲染。
- 当前质量门禁为 no-go 时默认使用 `legacy`；`procedural_v1` 仅作为明确标注的实验模式供手动选择，不得静默成为默认路径。
- WebGL/Canvas 相关逻辑优先封装在专用组件中，不和上传表单、API 调用混写。
- `ShaderPreview` 可以把第一帧 canvas 渲染结果作为 `Blob` 回传给页面容器。legacy 允许绑定原图纹理和动画；V1 必须按服务端规范化尺寸使用 WebGL1、`u_time=0`、不创建/绑定原图纹理重新编译，并从白名单 URL 读取服务端 PNG 计算 RGB RMSE（当前兼容阈值 0.02）。
- `RunProgress` 只展示阻塞式请求的执行中阶段说明和完成后的模式、档位、迭代、停止原因、候选与双端复核；它不是实时轮询器。`unscored_fallback=true` 时必须显示“WebGL fallback”，不得标成 `current_best`，且不渲染虚假的评分面板。`ScoreSummary` 只展示 Backend 返回的确定性指标，不在前端重算 Oracle。
- 页面把当前 project ID 和最近最多 10 个项目保存在 `localStorage`，支持刷新恢复、新建、最近项目切换和清除；最近项目只是本地索引，不是后端项目管理。
- `ephemeral` 和 `degraded` 必须显示用户可理解提示。
- 新增复杂组件时，先确认能否拆成一个页面容器加少量纯展示组件。
- 不把搜索、Oracle、DSL、Prompt 等领域逻辑放进前端；这些属于 `src/shaderforge/` 或 `src/agent/`。

## 样式规则

- 保持工具型界面风格：信息密度适中、控件清晰、状态可扫描。
- 使用稳定尺寸约束，避免上传图片、预览结果或错误消息导致布局跳动。
- 新增样式优先复用已有 class；只有跨多个组件复用时再抽通用样式。

## 验证

- 前端改动至少运行：

```bash
npm --prefix frontend run build
npm --prefix frontend run e2e:procedural-v1
npm --prefix frontend run e2e:memory
```

- 涉及后端接口字段时，同时检查 `frontend/src/api/` 类型和 `backend/app/schemas/` 是否一致。
- 两条 E2E 默认使用隔离端口 `15173/18088` 和 `15174/18089`，不会复用或终止开发中的 `5173/8088` 服务；需要并行运行时可通过脚本内对应 `SHADERGEN_*_E2E_*_PORT` 环境变量覆盖。
- 前端目录、组件约定、API 封装或样式规则变化时，同步更新本文档。
