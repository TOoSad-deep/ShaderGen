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
- 当前 `src/api/shader.ts` 封装 GLSL 生成和渲染评审；评审请求上传原图、canvas 渲染图和 GLSL。
- Generate/Review 都传递 `project_id` 并读取 `memory_status`；清除项目记忆只通过 `clearProjectMemory()`。

## 组件规则

- 页面级状态留在页面或 `App`，可复用组件只处理自身交互和渲染。
- WebGL/Canvas 相关逻辑优先封装在专用组件中，不和上传表单、API 调用混写。
- `ShaderPreview` 可以把第一帧 canvas 渲染结果作为 `Blob` 回传给页面容器；组件本身不调用后端。
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
npm --prefix frontend run e2e:memory
```

- 涉及后端接口字段时，同时检查 `frontend/src/api/` 类型和 `backend/app/schemas/` 是否一致。
- 前端目录、组件约定、API 封装或样式规则变化时，同步更新本文档。
