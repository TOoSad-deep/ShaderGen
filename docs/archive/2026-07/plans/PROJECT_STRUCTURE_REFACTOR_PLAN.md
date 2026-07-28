# 项目结构重构计划

> 归档状态：2026-07 早期目录重构建议，不是当前任务或已批准迁移。

状态：`proposed`

日期：2026-07-23

本计划用于规范 ShaderGen 的项目结构、依赖方向和模块可读性。当前只完成只读审阅与规划，不代表已经批准目录迁移、公共命名变更或契约调整。

## 目标

- 保持 `scene_mvp` 当前行为、HTTP 契约、Graph 语义、Artifact 和数据库语义稳定。
- 让组合根、应用编排、领域实现、基础设施和 UI 状态的职责清晰可见。
- 把巨型文件拆成可独立理解、测试和评审的小模块。
- 统一包发现、开发依赖、本地命令和 CI 验证边界。
- 让测试、脚本和文档能够按当前产品事实定位，而不是依赖历史命名猜测用途。

## 当前事实与主要问题

- 仓库已有 `frontend -> backend -> agent -> shaderforge` 四层边界，不需要从零重建顶层目录。
- 唯一产品路径称为 `scene_mvp`，Graph 和实现主要称为 `png_to_shader_min`，领域对象又大量使用 `Min*`；命名迁移不能与结构拆分混在同一个阶段。
- `src/agent/app/nodes/png_to_shader_min/runtime.py`、`src/shaderforge/rendering/webgl1_renderer.py`、`backend/app/services/shader_generation.py`、`frontend/src/App.tsx` 等文件聚合了过多职责。
- Graph 模块同时包含拓扑、默认资源装配和平台入口；Agent Service 还直接依赖 Node 内部能力，组合根和依赖方向不够清晰。
- Agent typed contract 经过 `shaderforge.public` 聚合入口，存在加载 Renderer 等重实现的风险。
- `pyproject.toml` 手工维护全部 Python package；`backend` 位于仓库根，而 `agent`、`shaderforge` 位于 `src/`，新增子包时容易漏入 wheel。
- Backend 用例直接依赖 HTTP Schema；前后端各自手工维护生成、进度和 Artifact 类型，缺少契约一致性门禁。
- 单元测试和运维脚本主要平铺；少数测试、契约版本仍带 `v1`，但并不等于已经删除的 V1 产品方案，不能做全局字符串替换。
- `make check` 是当前主干门禁，但 Ruff、Mypy、集成测试和页面 E2E 仍分散在其他命令或 CI 中。
- Memory/checkpoint 实现和 PostgreSQL 数据按既有决策休眠保留，本次重构不得顺带改变 namespace、数据格式或保留策略。

## 执行原则

1. 每个阶段使用独立 PR；一个 PR 只处理一个清晰边界。
2. 先增加行为与依赖护栏，再移动代码。
3. 文件拆分时保留稳定 façade 和导入路径；删除兼容层必须另行决策。
4. Graph 拓扑与节点名默认不变；只要注册、节点或边发生变化，就同步 Graph ASCII、Mermaid、路由表和安全说明。
5. 结构拆分与产品命名迁移分开，避免机械 diff 掩盖行为变化。
6. 每阶段先记录基线，再运行对应验证；没有自动化覆盖的缺口写入 `PROGRESS.md`。

## 分阶段计划

### P0：决策门与基线

工作：

- 确认结构重构与当前 `F09 active` 的关系。
- 冻结本轮默认不改变的 HTTP path、JSON 字段、Graph ID、节点名、Artifact 名称/路径、数据库 Schema 和 Memory 语义。
- 明确现有 Python import 根是否必须兼容。
- 记录当前单元、集成、Graph、前端和静态检查基线。

退出条件：

- 必须确认项写入 `docs/DECISIONS.md`。
- `make check`、集成测试、页面 E2E、Ruff 和 Mypy 基线已记录。

### P1：契约与依赖护栏

工作：

- 扩充结构测试，明确 `shaderforge` 不依赖 Agent/Backend、Agent 不依赖 Backend、Backend 只能调用 Agent Service 公共面。
- 禁止 Agent Service 新增对 Node 私有实现和 LLM 实现层的依赖。
- 增加 typed contract 轻量导入测试，防止导入契约时加载 Playwright、Pillow 或 NumPy 等重运行时。
- 为 Backend/Frontend 生成契约增加一致性检查；是否采用 OpenAPI codegen 由 P0 决策决定。
- 先把巨型测试中的结构细节断言与行为断言分开，确保后续拆文件不会制造大面积假失败。

退出条件：

- 新依赖规则可由自动测试阻止回退。
- 当前产品行为测试不因结构护栏变化而改变。

### P2：构建、打包与验证入口

工作：

- 用受控 package discovery 替代手写 package 清单，保留 YAML、Prompt 和 SQL package data。
- 统一开发依赖声明，去除重复 Ruff/Mypy 配置。
- 修复 Makefile 重复执行 Mypy 等问题，明确 `check`、`lint`、`integration_tests` 和 E2E 的职责。
- 增加 wheel 构建、隔离安装、包导入和 package resource smoke test。
- 补齐 CI path filter，避免脚本、Makefile 或 Graph 配置变化绕过相关门禁。

退出条件：

- wheel 安装后可导入 `agent`、`shaderforge`、`backend.sql`，并读取所需 YAML/SQL。
- 本地与 CI 的验证矩阵在文档中一致。

### P3：组合根与依赖方向

工作：

- 将纯 Graph Builder、LangGraph 平台入口、默认资源装配和 Backend lifespan 组合分开。
- 通过 Protocol/typed port 向 Agent Service 注入 compiled graph、Artifact reader 和 Renderer lifecycle。
- 移除 Service 对 Node 私有 helper 的直接依赖。
- 让 Agent typed contract 依赖 ShaderForge 轻量类型模块，不经过重型聚合入口。
- 保持 Memory/checkpoint 休眠行为、实例创建边界和现有数据语义不变。

退出条件：

- 依赖矩阵不存在反向边。
- `make docs-check`、`uv run langgraph validate` 和 Graph 集成测试通过。
- Memory 相关 package resource 与休眠生命周期测试通过；若连接 PostgreSQL，追加 `make test-memory-postgres`。

### P4：拆分 Agent 核心

优先拆分 `nodes/png_to_shader_min/runtime.py`，建议职责如下：

```text
nodes/png_to_shader_min/
├── factory.py
├── renderer_registry.py
├── evaluation.py
├── lifecycle_nodes.py
├── author_nodes.py
├── optimization_nodes.py
└── decision_nodes.py
```

同时逐步拆分 `services/png_to_shader_min.py` 的事件映射、结果映射、Artifact 读取和 Graph 驱动职责。原包入口保留稳定 re-export。

退出条件：

- Graph 节点、边、路由结果和 `current_best` 安全边界没有变化。
- 原有行为断言无需改写即可通过。
- 单元、Graph 集成、docs-check、LangGraph validate、Ruff 和 Mypy 全部通过。

### P5：拆分 ShaderForge 与 Backend

ShaderForge 按顺序拆分 Renderer、Optimizer、Template/Validation；每次只拆一个领域模块，并保留原公共入口。

Backend 建议逐步形成：

```text
backend/app/
├── application/
│   └── shader_generation/
├── repositories/
│   └── agent_process.py
├── services/
│   ├── project_locks.py
│   ├── agent_gateway.py
│   └── run_artifacts.py
└── api/
```

先引入应用 DTO/port，再把过程账本从 Service 层迁到 Repository；HTTP Schema 只留在 API 边界。

退出条件：

- ShaderForge 不新增对 Agent、Backend 或 FastAPI 的依赖。
- Route、应用用例、Repository 的依赖方向由结构测试覆盖。
- Renderer、Backend 生命周期、过程账本和 API 集成测试通过。

### P6：整理 Frontend、Tests、Scripts 和 Docs

Frontend 按产品能力形成 feature slice，优先抽出运行状态机、进度 reducer、timeout/abort、API error parser 和 preview runtime；增加纯逻辑单元测试，浏览器 Renderer 继续由 E2E 验收。

Tests 按 `agent`、`shaderforge`、`backend` 与跨组件集成能力归位。Scripts 按 `checks`、`database`、`e2e`、`diagnostics` 分类，Makefile 保持稳定用户入口。

为历史规格增加明确的 current/superseded/historical 索引；不重写旧 ADR，不移动仍被 evidence registry 引用的证据路径。

退出条件：

- 前端 build、单元测试与 scene_mvp E2E 通过。
- Makefile、README、AGENTS、组件 README 和证据索引与新目录一致。
- `scripts/docs_check.py` 的职责得到拆分或形成可维护的规则模块。

### P7：可选命名空间迁移

前六阶段稳定后，再单独评估：

- `agent` 是否迁为 `shadergen.agent`。
- `backend` 是否移入统一 `src/` 布局。
- `png_to_shader_min` 是否统一命名为 `scene_mvp`。
- 是否删除兼容 façade 和单产品下的 `generation_mode` 字段。

该阶段可以得出“不迁移”的结论。若迁移，必须单独冻结兼容策略，并同步 Graph 注册、全部文档、测试、外部导入和证据引用。

## 默认验证矩阵

每个 PR：

```bash
git diff --check
uv run ruff check .
uv run mypy --strict src backend
make check
```

按范围追加：

```bash
uv run pytest tests/integration_tests
make test-scene-mvp-ui
make test-memory-postgres
uv build --wheel
```

Graph 相关改动始终显式执行：

```bash
make docs-check
uv run langgraph validate
```

## 开始实施前必须确认

1. 是否暂停 F09，将“项目结构重构”登记为唯一 `active` 功能；还是把结构重构作为 F09 内部工程增量。
2. 是否要求保持 `agent`、`shaderforge`、`backend` 三个 Python import 根、`png_to_shader_min` Graph ID、现有 HTTP/Artifact 契约完全兼容。
3. 是否接受本计划的保守顺序：先横向依赖与组合根治理，再逐层拆文件；暂不直接改为完整的 `pipelines/scene_mvp` 垂直切片。
4. 前后端契约选择 OpenAPI TypeScript codegen，还是继续手写类型并增加 parity gate。
5. Backend 是否批准新增正式 `application` 和 `repositories` 层。
6. Ruff/Mypy 目标是立即全仓严格通过，还是先对改动文件增量强制、再清理存量。
