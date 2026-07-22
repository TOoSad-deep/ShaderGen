# Frontend

前端负责用户输入、图片预览、Shader/WebGL 预览、生成结果展示和用户交互状态。前端不负责模型 Prompt 组装、Shader 搜索、评分、持久化或后端业务规则。

## 目录规范

- `src/api/client.ts`：统一解析 `VITE_API_BASE_URL`、发起 `fetch` 并提取安全错误消息。
- `src/api/`：领域 API 封装。页面和组件不得直接调用 `fetch` 或拼接后端 URL。
- `src/components/`：可复用 UI 和 WebGL 预览组件。组件只接收 props，不直接了解后端业务流程。
- `src/pages/`：页面级组合。页面可以管理页面状态和调用 `src/api/`。
- `src/styles/`：全局样式和页面样式。避免把大量内联样式写进组件。
- `public/`：静态资源。

## 本地配置

- Agent、Backend 和仓库脚本的服务端变量放在根目录 `.env`，以根目录 `.env.example` 为模板。
- Vite 公开变量放在 `frontend/.env.local`，以 `frontend/.env.example` 为模板；一次性覆盖也可以在运行 `make dev-frontend` 或 `npm --prefix frontend run build` 的 shell 中设置。
- Vite 不使用根目录 `.env` 作为前端配置来源。所有 `VITE_*` 变量都会暴露到浏览器构建产物，禁止写入模型、数据库或 LangSmith 密钥，也不要提交本地 `.env.local`。
- 当前公开变量只有 `VITE_API_BASE_URL` 和 `VITE_GENERATION_REQUEST_TIMEOUT_MS`；前者默认指向本地 Backend，后者留空时按质量档位使用内置等待上限。

## API 规则

- 所有后端调用集中在 `src/api/`，并经 `src/api/client.ts` 的 `apiFetch()` / `resolveApiUrl()` 访问；组件和页面不得绕过该边界。
- API base URL 通过 `VITE_API_BASE_URL` 配置，默认指向 `http://127.0.0.1:8088`，只用于本地开发。
- API 函数返回明确的 TypeScript 类型。
- 后端错误要转成用户能理解的错误消息；不要把原始异常对象直接展示给用户。
- 当前 `src/api/shader.ts` 封装 V1 GLSL 生成、Artifact URL 解析和项目记忆清理。Generate 始终显式发送 `generation_mode`（`procedural_v1` 或 `scene_mvp`，由页面模式选择器决定，默认 `procedural_v1`），并发送质量档位、补充约束和 `project_id`；前端不再调用独立 Review 接口。
- `src/api/nodeLab.ts` 只封装默认关闭的 `/api/lab/v1/*` 调试边界；`/lab` 工作台读取后端 descriptor 和调用示例，不在前端复制节点输入规则、Fixture 或 benchmark 判定。步骤列表直接消费后端返回的 DAG summary，只在用户选中步骤时读取完整 detail，禁止为每个步骤产生 N+1 请求。
- `procedural_v1` 读取 run/current_best/score/stop/render 尺寸和白名单 Artifact URL；相对 URL 必须经 `resolveShaderApiUrl()` 解析，不在组件中手拼 API base。
- `scene_mvp` 在 `ShaderResponse` 上读取可选 `min_pipeline`（整图 `mae`、`objective_loss`、局部 `metric_breakdown`、`template_version`、render/LLM 的实际值与预算、Refine 预算、`scene`、`trace`，以及 `target_loss`、`target_reached` 与 prepared 性能字段）；所有字段按可缺省处理，缺省时展示占位而不是报错。
- 生成失败兼容旧版 `detail: string` 和类型化 `detail: {message, code, run_id, stage, retryable, stop_reason}`；页面必须展示可用于后端检索的 Run ID 和失败阶段，不把服务端超时误报为客户端输入错误。
- Generate 使用 `AbortController` 支持“停止等待”和浏览器端兜底超时。它只中止客户端 HTTP 等待，不等价于服务端取消；自动等待上限可通过 `VITE_GENERATION_REQUEST_TIMEOUT_MS`（毫秒，最小 10000）覆盖。
- Generate 传递 `project_id` 并读取 `memory_status` 和自动闭环 Review；清除项目记忆只通过 `clearProjectMemory()`。
- `quality_preset` 和 `instruction` 始终属于当前 V1 路径；除请求执行期间外，两个输入都必须可编辑。

## 组件规则

- 页面级状态留在页面或 `App`，可复用组件只处理自身交互和渲染。
- 产品页面提供 `procedural_v1`（默认）和 `scene_mvp` 两种生成模式，由“生成模式”选择器切换；两种模式均为实验功能，页面必须保留清晰的实验性提示。
- `scene_mvp` 结果由 `SceneMvpSummary` 纯展示组件渲染：复合损失与目标、整图/前景/高光/阴影 MAE、基于 `target_reached` 的“质量达标/流程完成未达标”提示、渲染/LLM 实际值与预算、run_id、停止原因、prepared 性能、场景 JSON 和阶段追踪；最终 Render 与 GLSL 预览继续复用既有面板。
- WebGL/Canvas 相关逻辑优先封装在专用组件中，不和上传表单、API 调用混写。
- `ShaderPreview` 只执行 V1 静态预览：按服务端规范化尺寸使用 WebGL1、固定 `u_time=0`、不创建或绑定原图纹理，并从白名单 URL 读取服务端 PNG 计算 RGB RMSE（当前兼容阈值 0.02）。
- `RunProgress` 只展示阻塞式 V1 请求的执行中阶段说明和完成后的档位、迭代、停止原因、候选与双端复核；它不是实时轮询器。`unscored_fallback=true` 时必须显示“WebGL fallback”，不得标成 `current_best`，且不渲染虚假的评分面板。`ScoreSummary` 只展示 Backend 返回的确定性指标，不在前端重算 Oracle。
- 页面把当前 project ID 和最近最多 10 个项目保存在 `localStorage`，支持刷新恢复、新建、最近项目切换和清除；最近项目只是本地索引，不是后端项目管理。
- `ephemeral` 和 `degraded` 必须显示用户可理解提示。
- 新增复杂组件时，先确认能否拆成一个页面容器加少量纯展示组件。
- 不把搜索、Oracle、DSL、Prompt 等领域逻辑放进前端；这些属于 `src/shaderforge/` 或 `src/agent/`。

## Node Lab 工作台

- `/lab` 是本地调试页面，不改变产品 `/api/shader/*` 契约。后端必须通过 `make dev-node-lab` 显式启用；普通 `make dev-backend` 下页面会显示稳定的 API 未开放错误。
- 左侧目录来自 20 个 Node descriptor（包括确定性 `prepare_measurement_seed`）；中间输入编辑器使用机器可读示例、执行模式、Fixture、`base_step_id` 和显式模型门禁；右侧只展示安全 Output、State Diff、diagnostics/usage/provenance。
- 页面支持新建或恢复 LabRun、上传同 Run 私有 Artifact、从任意父步骤分支、下载不透明 Artifact，并在底部根据 summary 重建不可变步骤 DAG；完整步骤结果按选择惰性加载并缓存，Artifact descriptor 独立维护，不与 step list 混用。
- Real 模型步骤仍需服务端环境开关、页面单步确认和 Backend/Application 三层校验。页面不会自动开启真实模型，也不提供 `project_commit` 选项。

## 样式规则

- 保持工具型界面风格：信息密度适中、控件清晰、状态可扫描。
- 使用稳定尺寸约束，避免上传图片、预览结果或错误消息导致布局跳动。
- 新增样式优先复用已有 class；只有跨多个组件复用时再抽通用样式。

## 验证

- 所有前端源码、配置和构建边界改动至少运行 `npm --prefix frontend run build`。
- 按受影响范围追加浏览器门禁：

| 改动范围 | 追加命令 |
|---|---|
| 产品上传、生成、结果或 Shader 预览 | `npm --prefix frontend run e2e:procedural-v1` |
| scene_mvp 模式、运行摘要或 min_pipeline 字段展示 | `npm --prefix frontend run e2e:scene-mvp` |
| project 切换、Memory 状态或清理 | `npm --prefix frontend run e2e:memory` |
| `/lab` 页面、Node Lab API 封装或 DAG/Artifact 交互 | `npm --prefix frontend run e2e:node-lab` |

跨多个范围时运行对应命令的并集；纯样式或与某条流程无关的局部组件改动不强制运行全部 E2E，但必须说明选择依据。

- 涉及后端接口字段时，同时检查 `frontend/src/api/` 类型和 `backend/app/schemas/` 是否一致。
- 四条 E2E 默认分别使用隔离端口 `15173/18088`、`15176/18091`、`15174/18089` 和 `15175/18090`，不会复用或终止开发中的 `5173/8088` 服务；需要并行运行时可通过脚本内对应 `SHADERGEN_*_E2E_*_PORT` 环境变量覆盖。Node Lab 页面 E2E 只连接假 API，不调用模型、Renderer、Memory 或产品 run；scene_mvp E2E 同样只连接本地假 API，覆盖质量达标与未达标两种响应。
- 前端目录、组件约定、API 封装或样式规则变化时，同步更新本文档。
