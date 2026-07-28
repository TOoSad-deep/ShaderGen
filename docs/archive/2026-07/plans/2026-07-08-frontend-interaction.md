# Frontend Interaction Implementation Plan

> 归档状态：历史实施基线，不得按下方 checkbox 或 worker 指令重新实施。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 优化现有“上传图片 -> 生成 GLSL -> WebGL 预览”单页交互。

**Architecture:** 保留 `App.tsx` 单页和现有 `generateShader(file)` API。用 React 本地状态管理上传、拖拽、生成、复制和错误状态，样式仍放在 `frontend/src/styles/app.css`。

**Tech Stack:** React、TypeScript、Vite、原生文件输入、原生 Clipboard API、CSS。

## Global Constraints

- 不新增 UI 组件库或图标依赖。
- 不实现 F01 的多字段任务提交表单。
- 不改变 `POST /api/shader/generate` 的请求或响应。
- 前端改动至少运行 `npm --prefix frontend run build`。
- 当前目录没有 Git 元数据，不能创建 worktree 或 commit。

---

### Task 1: 优化上传生成页面

**Files:**
- Modify: `frontend/src/api/shader.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles/app.css`

**Interfaces:**
- Consumes: `generateShader(file: File): Promise<{ glsl: string }>`。
- Produces: 单页 UI，支持点击上传、拖拽上传、忙碌态、文件信息、错误提示、复制 GLSL；API wrapper 将 FastAPI `detail` 转为用户可读错误。

- [x] **Step 1: 写最小交互检查**

没有前端测试框架，不新增依赖。用构建和浏览器检查覆盖交互是否可用。

Run:

```bash
npm --prefix frontend run build
```

Expected: exit 0。

- [x] **Step 2: 实现最小页面状态**

在 `App.tsx` 中保留现有 API 调用，增加 `fileInfo`、`dragActive`、`copied`，并把上传入口改成拖拽/点击区域。

- [x] **Step 3: 改善错误消息**

在 `frontend/src/api/shader.ts` 中解析 FastAPI JSON 错误体里的 `detail` 字段，不改变请求或响应成功契约。

- [x] **Step 4: 调整样式**

在 `app.css` 中为上传区、状态摘要、按钮、结果区和移动端布局增加样式，保持稳定尺寸。

- [x] **Step 5: 验证构建**

Run:

```bash
npm --prefix frontend run build
```

Expected: exit 0。

### Task 2: 记录结果

**Files:**
- Modify: `PROGRESS.md`

**Interfaces:**
- Consumes: 构建验证结果。
- Produces: 本次前端交互实现和验证记录。

- [x] **Step 1: 更新进度**

在 `PROGRESS.md` 备注中记录实现范围和验证命令结果。

- [x] **Step 2: 复跑验证**

Run:

```bash
npm --prefix frontend run build
```

Expected: exit 0。
