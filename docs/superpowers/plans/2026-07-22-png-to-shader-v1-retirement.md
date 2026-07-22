# PNG-to-Shader V1 分阶段退役与清理计划

**目标：** 在不破坏当前默认产品链路、Node Lab、Memory、质量证据和历史可追溯性的前提下，逐步解除 `png_to_shader_min` 对 V1 命名空间的依赖，完成 `scene_mvp` 替代门禁，最后删除 PNG-to-Shader V1 独立有界 LangGraph 的可执行代码。

**当前结论：** 本计划不授权立即删除 V1。当前 `procedural_v1` 仍是 Backend/Frontend 默认模式，Node Lab、Memory 和正式 benchmark 仍绑定 V1；`scene_mvp` 尚未通过新的真实模型 benchmark 与独立人工门禁。只有本计划的 G0–G5 门禁依次通过并新增正式下线决策后，才能执行删除阶段。

**实施原则：** 先解耦共享基础设施，再补齐替代能力；先切换默认并保留回退，再删除旧实现；历史决策和冻结证据只增不改，不与可执行代码一起删除。

## 1. 范围

### 1.1 计划最终退役的 V1 能力

- `png_to_shader_v1_graph` 独立有界 LangGraph 及其 routing。
- `agent.app.nodes.png_to_shader_v1` 的模型、确定性节点和 V1 Node Lab Provider。
- V1 Agent Service、Parser、业务契约和 V1 专属 Prompt。
- Backend 的 `procedural_v1` 执行分支和 Frontend 的 V1 生成入口。
- V1 专属在线/离线执行脚本、普通测试、E2E 和当前运行型 benchmark 命令。
- `langgraph.json` 中的 `png_to_shader_v1` 注册。

### 1.2 不属于删除范围的共享能力

- `LLMGateway`、模型配置和通用 Prompt loader。
- `LocalArtifactStore`、项目/run 总账和历史 Artifact 读取能力。
- WebGL1 Renderer、prepared uniform 热路径和通用 Shader 校验。
- 可被 min 复用的稳定 JSON、文本块和多模态消息构造工具。
- ShaderForge 中与 V1 Graph 无关的通用 scene、generation、rendering、evaluation 和 optimization 能力。
- Node Lab 通用 Harness 内核；是否保留产品级 Node Lab 由 G2 决策决定。

### 1.3 永久保留或只读归档的内容

- `docs/DECISIONS.md` 中已有决策及其 superseded 关系。
- `docs/progress/archive/`、`docs/evidence/registry.json` 和冻结证据摘要/hash。
- 正式 V1 benchmark manifest、gate policy、人工评审摘要和失败证据；允许迁入明确的 archive，禁止删除或覆盖。
- 已发布数据库 migration 和历史 run/Artifact 的必要只读兼容。
- 可复用于 min benchmark 的参考 PNG、golden 输入和合法授权测试资产。

## 2. 非目标

- 本计划不实现新的 scene、模板、objective 或 CMA-ES。
- 本计划不以删文件数量作为完成标准。
- 本计划不在迁移期修改既有 V1 质量结果或重新解释历史 gate。
- 本计划不默认决定删除 Node Lab 或 Memory；两者必须分别做显式架构决策。
- 本计划不把 `scene_mvp` 的流程完成等同于质量达标或发布批准。
- 本计划不在同一个增量中同时进行固定模板扩展和 V1 大规模删除。

## 3. 当前依赖基线

### 3.1 产品路径

```text
Frontend 默认 procedural_v1
  -> POST /api/shader/generate
  -> Backend generation_mode 分流
  -> PngToShaderV1Service
  -> png_to_shader_v1_graph
```

`scene_mvp` 当前是显式实验模式，不能在未完成切换门禁时被视为默认替代。

### 3.2 V1 外围依赖

```text
png_to_shader_v1
├── Backend 默认 Service 与生命周期
├── Frontend 默认模式与 E2E
├── PostgreSQL checkpoint / Memory / F08
├── Node Lab 默认 Provider / H02
├── M5 benchmark / 人工 gate / 历史证据
├── langgraph.json 注册
└── 大量单元、集成和架构边界测试
```

### 3.3 min 对 V1 命名空间的反向依赖

- `png_to_shader_min/model_author.py` 使用 V1 消息模块的 `canonical_json`。
- `png_to_shader_min/runtime.py` 使用 V1 消息模块的 `text_part`、`labeled_image_parts` 和 `multimodal_human_message`。
- `shaderforge.rendering`、`shaderforge.validation` 和测量模块仍从 `shaderforge.contracts.png_to_shader_v1` 取得 WebGL1 通用运行契约。

在这些依赖清零前，不得按 V1 文件名直接批量删除。

## 4. 总门禁

| 门禁 | 通过条件 | 未通过时允许的动作 |
|---|---|---|
| G0：计划与冻结 | 本计划入库；文档明确 V1 只做兼容/安全修复 | 只允许解耦和文档工作 |
| G1：共享依赖解耦 | min、Renderer、validator 不再导入 V1 业务命名空间 | 不得切换默认模式 |
| G2：外围能力决策 | Node Lab、Memory、历史 Artifact 兼容分别有明确迁移/退役决策 | 不得删除 V1 Provider/Service |
| G3：min 替代证据 | 固定 manifest、AI-off、真实模型 benchmark、独立人工门禁通过 | 不得把 min 设为唯一默认 |
| G4：产品切换 | Backend/Frontend 默认 min，兼容期 E2E、生命周期和 Artifact 验收通过 | 必须保留 V1 回退入口 |
| G5：下线授权 | 兼容期证据稳定；新增正式下线决策并冻结删除清单 | 不得删除 V1 可执行代码 |

任何门禁失败都不得通过降低测试、删除失败证据或放宽质量阈值来绕过。

---

## Task 0：冻结 V1 变更面并建立删除清单

**目标：** 把 V1 从“继续演进的实现”变为“冻结的兼容路径”，并建立可审计的依赖清单。

**修改文件：**

- Modify: `docs/DECISIONS.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `src/agent/app/nodes/png_to_shader_v1/ARCHITECTURE.md`
- Modify: `PROGRESS.md`
- Create: `docs/evidence/png-to-shader-v1-retirement-inventory.json`

- [ ] 记录新的退役准备决策：V1 不再新增算法能力，只接受安全、兼容和阻塞性修复。
- [ ] 生成机器可读 inventory，至少记录文件路径、模块类型、直接消费者、共享/专属分类和计划处置方式。
- [ ] 为所有 V1 Prompt、Graph node、Provider descriptor、脚本、Make target、测试和 benchmark 建立一一对应清单。
- [ ] 记录当前 `git ls-files` 和关键源码 SHA-256；忽略 `__pycache__`、本地 output 和其他未跟踪运行缓存。
- [ ] 明确当前活跃的 `scene_mvp` 增量不得和 V1 删除混在同一次改动中。

**验证：**

```bash
uv run python scripts/docs_check.py
uv run pytest tests/unit_tests/test_agent_architecture_boundaries.py -q
git diff --check
```

**完成标准：** G0 通过；没有删除任何 V1 运行代码。

---

## Task 1：提取 min 仍在使用的通用消息工具

**目标：** 消除 `png_to_shader_min` 对 `agent.app.messages.png_to_shader_v1` 的依赖。

**建议文件：**

- Create: `src/agent/app/messages/structured_multimodal.py`
- Modify: `src/agent/app/messages/png_to_shader_v1.py`
- Modify: `src/agent/app/messages/__init__.py`
- Modify: `src/agent/app/nodes/png_to_shader_min/model_author.py`
- Modify: `src/agent/app/nodes/png_to_shader_min/runtime.py`
- Modify: V1 消费这些 helper 的模型节点
- Modify: `src/agent/app/messages/ARCHITECTURE.md`
- Modify: `tests/unit_tests/test_agent_architecture_boundaries.py`

**迁移内容：**

- `canonical_json`
- `text_part`
- `labeled_image_parts`
- `multimodal_human_message`
- 仅在确认无 V1 业务契约依赖后迁移通用 SHA-256 helper

**保留在 V1 模块中的内容：**

- `CandidateRecordInput`、`RenderEvidenceBinding` 相关验证。
- V1 ContextPack、GLSL 和候选证据绑定逻辑。

- [ ] 先写架构边界测试，拒绝 `nodes/png_to_shader_min/**` 导入 `messages.png_to_shader_v1`。
- [ ] 把通用 helper 搬到中立模块，保持序列化结果逐字节兼容。
- [ ] 更新 min 与 V1 消费者，不保留 min 到 V1 的兼容导入。
- [ ] 为重复 key、非有限数、Pydantic/dataclass/Enum 序列化和多模态 part 顺序补充回归测试。

**验证：**

```bash
uv run pytest tests/unit_tests/test_png_to_shader_min.py \
  tests/unit_tests/test_png_to_shader_v1_nodes.py \
  tests/unit_tests/test_png_to_shader_v1_parsers.py \
  tests/unit_tests/test_agent_architecture_boundaries.py -q
rg -n "agent\.app\.messages\.png_to_shader_v1" \
  src/agent/app/nodes/png_to_shader_min
```

最后一条命令预期无输出。

---

## Task 2：把通用 WebGL1 契约移出 V1 业务包

**目标：** 保留 min 所需的 Renderer/validator 能力，同时允许后续删除 V1 Agent 业务契约。

**建议文件：**

- Create: `src/shaderforge/contracts/webgl1.py`
- Modify: `src/shaderforge/contracts/png_to_shader_v1.py`
- Modify: `src/shaderforge/contracts/__init__.py`
- Modify: `src/shaderforge/rendering/webgl1_renderer.py`
- Modify: `src/shaderforge/validation/shader_validator.py`
- Modify: `src/shaderforge/analysis/measurements.py`
- Modify: `src/shaderforge/public.py`
- Modify: `src/shaderforge/contracts/ARCHITECTURE.md`
- Modify: `tests/unit_tests/test_agent_architecture_boundaries.py`

**迁移内容：**

- `RenderContract`
- `WEBGL1_STATIC_NO_TEXTURE_V1`
- 只与 WebGL1 运行契约有关、与 V1 Graph 预算/候选无关的类型

**约束：**

- `contract_id=webgl1_static_no_texture_v1` 暂不改名，避免破坏历史 manifest 和 Artifact 身份。
- V1 业务契约可以反向导入通用 WebGL1 契约；通用 Renderer 不得反向导入 V1 业务契约。
- 不在本任务修改 GLSL 语义、Renderer 像素方向或 forbidden token。

- [ ] 先写依赖方向测试。
- [ ] 移动通用契约并保持对象 identity/序列化兼容。
- [ ] 更新 Renderer、validator、measurements 和 public facade。
- [ ] 验证 legacy render 与 prepared render 像素结果不变。

**验证：**

```bash
uv run pytest tests/integration_tests/test_webgl1_renderer.py \
  tests/integration_tests/test_measurement_affine_seed.py \
  tests/unit_tests/test_png_to_shader_min.py \
  tests/unit_tests/test_png_to_shader_v1_m0.py -q
rg -n "shaderforge\.contracts\.png_to_shader_v1" \
  src/shaderforge/rendering \
  src/shaderforge/validation \
  src/shaderforge/analysis
```

最后一条命令预期无输出。Task 1 和 Task 2 完成后，G1 才可通过。

---

## Task 3：冻结 min 的独立质量基线和发布门禁

**目标：** 在停止 V1 benchmark 之前，让 `scene_mvp` 拥有不依赖 V1 执行代码的独立质量证据。

**建议文件：**

- Create: `benchmarks/png_to_shader_min/manifest.yaml`
- Create: `benchmarks/png_to_shader_min/gate.yaml`
- Create: `benchmarks/png_to_shader_min/README.md`
- Create: `scripts/run_png_to_shader_min_benchmark.py`
- Create/Modify: min benchmark models、manifest parser 和 gate
- Modify: `Makefile`
- Modify: `docs/evidence/registry.json`
- Create: min benchmark 单元和集成测试

**要求：**

- manifest 必须固定输入、外部同口径指标、质量档位、模型预算、draw 预算和源码指纹。
- AI-off、真实模型和 evaluate 三种模式必须分离。
- 真实模型只允许在显式 `--allow-model-calls` 和整 run 硬预算下执行。
- 失败产物只增不改，不能覆盖 V1 或 min 的既有 run。
- 人工评审必须盲化，至少比较 fallback、Initial 和 final，记录 tie。
- 指标必须覆盖 global、foreground/background、geometry、edge 和 worst-tile；最终版本以固定模板扩展冻结的 objective 为准。
- V1 参考 PNG 可以迁入共享数据目录或由 min manifest 引用，但不得删除历史 V1 manifest/gate。

- [ ] 建立 min AI-off smoke。
- [ ] 建立固定真实模型 manifest 与预算门禁。
- [ ] 建立独立人工评审页面/JSON Schema。
- [ ] 冻结 min gate 阈值，禁止根据结果事后调整。
- [ ] 实际运行新的真实模型 benchmark 和独立人工盲评。
- [ ] 将完整 run 摘要、hash 和 `durability_status` 登记到 evidence registry。

**计划命令：**

```bash
make benchmark-scene-mvp-ai-off
make benchmark-scene-mvp QUALITY_PRESET=balanced MODEL_CALL_BUDGET=<hard-limit>
make benchmark-scene-mvp-gate \
  BENCHMARK_OUTPUT=<run-dir> \
  HUMAN_REVIEW=<review.json>
```

**完成标准：** 自动 gate 和独立人工门禁都通过；只完成无模型 smoke 不算 G3 通过。

---

## Task 4：决定并迁移 Node Lab 边界

**目标：** 在删除 V1 Provider 前，明确 H02 是迁移到 min 还是正式退役。

### 方案 A：迁移到 min，默认推荐

**建议文件：**

- Create: `src/agent/app/nodes/png_to_shader_min/integrations/node_lab/`
- Create: min Provider registry、deterministic/model Adapter 和 fixture
- Create: `benchmarks/node_lab/png_to_shader_min/`
- Modify: `src/agent/app/services/node_lab.py`
- Modify: Node Lab suites、CLI、Fake API 和 UI descriptor
- Modify: Node Lab 单元、集成和浏览器 E2E

- [ ] 为 12 个 min Graph 节点建立真实 target descriptor。
- [ ] Adapter 只转换 Lab JSON/Artifact，不复制 runtime 语义。
- [ ] 为 Initial/fallback 仲裁、feature queue、prepared render、best selection 和 finalize 建立场景 suite。
- [ ] 模型节点提供 fixture 和显式 real 开关。
- [ ] 默认 Node Lab Provider 切换到 min。
- [ ] 更新 H02 验收证据。

### 方案 B：正式退役产品级 Node Lab

- [ ] 新增决策说明为什么通用 Harness 不再是项目能力。
- [ ] 将 H02 从当前功能状态迁入历史记录，而不是静默删除 passing 证据。
- [ ] 删除 Backend/Frontend Node Lab 产品入口前完成 API 和文档兼容审计。
- [ ] 保留已有 Node Lab 历史 benchmark 与证据 hash。

**决策门：** 用户必须在 A/B 中明确选择；不得由实现者自行猜测。

**方案 A 验证：**

```bash
make benchmark-node-lab-ai-off
make benchmark-node-lab-model
make test-node-lab-ui
```

所有命令必须已经指向 min Provider，不能继续依赖 V1 target 才算迁移完成。

---

## Task 5：决定并迁移 Memory/Checkpoint 边界

**目标：** 在删除 `PngToShaderV1Service` 前处理 F08、Backend startup 和旧 checkpoint。

### 方案 A：为 min 建立新的耐久 Memory

- 为 min 单独定义哪些轻量状态允许 checkpoint，禁止图片、Scene、GLSL、RGB、render 和模型原始响应进入 checkpoint。
- 定义策略记忆的版本、选择条件、项目隔离和清除语义。
- Backend lifecycle 注入 min checkpointer/store。
- 新旧 key 前缀必须隔离；禁止把 V1 payload 当作 min scene 恢复。
- 建立 PostgreSQL 重建、项目隔离、清除和异常补偿测试。

### 方案 B：min 明确不使用 Agent Memory

- 新增决策说明 min 的确定性 scene/search 为什么不消费旧策略 Memory。
- Backend 启动不得再仅为 V1 创建 Agent Memory。
- 定义旧 V1 checkpoint 的保留期、只读/删除接口和数据迁移责任。
- F08 迁入历史能力记录，不能继续表述为当前 min 能力。

**决策门：** 用户必须明确选择 A/B，并确认历史 checkpoint 的保留策略。

**验证至少包括：**

```bash
make test-memory-postgres
uv run pytest tests/unit_tests/test_backend_lifecycle.py -q
uv run pytest tests/integration_tests/test_png_to_shader_v1_api.py \
  tests/integration_tests/test_shader_memory_postgres.py -q
```

迁移完成后应以新的 min 测试替换执行型 V1 测试，但历史测试结果仍保留在证据中。

Task 4 和 Task 5 都有明确决策并完成相应迁移后，G2 才可通过。

---

## Task 6：把 `scene_mvp` 切换为默认产品路径，保留 V1 回退

**目标：** 验证 min 可以承担真实默认流量，但暂不删除 V1。

**修改文件：**

- Modify: `backend/app/api/routes/shader.py`
- Modify: `backend/app/schemas/shader.py`
- Modify: `backend/app/services/shader.py`
- Modify: `backend/app/services/shader_generation.py`
- Modify: `backend/app/main.py`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/api/shader.ts`
- Modify: Frontend 模式说明和 README
- Modify: Backend/Agent 架构文档
- Modify: API、生命周期和浏览器 E2E

**过渡期契约：**

```text
默认：scene_mvp
显式回退：procedural_v1
历史 run：继续只读
V1 代码：冻结但仍可运行
```

- [ ] Backend 未传 `generation_mode` 时默认 `scene_mvp`。
- [ ] Frontend 初始模式改为 `scene_mvp`。
- [ ] 页面继续清晰标记质量状态和实验/发布边界。
- [ ] `procedural_v1` 暂时保留为显式回退，不自动静默切换。
- [ ] Scene mode 的 run progress、Artifact、账本和错误 envelope 完整验收。
- [ ] 历史 V1 run 的 Artifact URL 和 manifest 继续可读。
- [ ] 更新 fake API、OpenAPI、README 和所有默认值测试。

**验证：**

```bash
make check
uv run pytest -q tests/integration_tests
make test-scene-mvp-ui
npm --prefix frontend run e2e:procedural-v1
make test-memory-postgres
```

**回滚点：** 本阶段出现质量、稳定性或兼容问题时，只回滚默认模式；V1 代码仍完整保留。G4 通过前不得进入删除阶段。

---

## Task 7：完成兼容观察和正式下线授权

**目标：** 用冻结证据证明默认切换稳定，并把删除授权写成独立决策。

- [ ] 默认 min 路径通过固定 AI-off、真实模型和独立人工门禁。
- [ ] Backend 生命周期、浏览器 E2E、Artifact、进度轮询和错误路径稳定通过。
- [ ] Node Lab/Memory 决策已经落地，不存在 V1 唯一消费者。
- [ ] 对仓库执行完整 import/reference 扫描，剩余 V1 引用全部分类为“待删”或“历史保留”。
- [ ] 确认没有外部客户端仍依赖缺省 `procedural_v1`；如无法确认，保留显式兼容窗口。
- [ ] 新增 `docs/DECISIONS.md` 下线决策，写明删除版本、回滚点、历史数据策略和证据链接。
- [ ] 冻结删除前 commit/tag 或等价不可变源码指纹。

**删除前扫描：**

```bash
rg -n "png_to_shader_v1|procedural_v1|PNG-to-Shader V1" \
  src backend frontend tests scripts benchmarks docs \
  README.md PROGRESS.md Makefile langgraph.json pyproject.toml
```

扫描结果必须逐条进入 inventory，不能使用宽泛忽略规则掩盖运行时依赖。

**完成标准：** 新下线决策已批准，G5 通过。

---

## Task 8：删除 V1 可执行路径

**前置条件：** G0–G5 全部通过。此任务必须是独立改动，不与新的 min 算法功能混合。

### 8.1 Graph、State 和 Service

- [ ] 删除 `src/agent/app/graphs/png_to_shader_v1_graph.py`。
- [ ] 删除 `src/agent/app/graphs/png_to_shader_v1_routing.py`。
- [ ] 从 `langgraph.json` 删除 `png_to_shader_v1` 注册。
- [ ] 删除 `PngToShaderV1State`，保留已迁移的共享状态类型。
- [ ] 删除 `src/agent/app/services/png_to_shader_v1.py`。
- [ ] 删除 Backend startup 中的 V1 Service 创建和关闭逻辑。

### 8.2 V1 Node、契约、Parser 和 Prompt

- [ ] 删除 `src/agent/app/nodes/png_to_shader_v1/`，前提是 min Node Lab 已迁移或 H02 已正式退役。
- [ ] 删除 `src/agent/app/contracts/png_to_shader_v1.py` 中剩余 V1 业务契约。
- [ ] 删除 `src/agent/app/parsers/png_to_shader_v1.py`。
- [ ] 删除 `src/agent/app/messages/png_to_shader_v1.py` 中剩余 V1 业务绑定。
- [ ] 删除 V1 专属 Prompt：`visual_analysis_v1`、`shader_author_*_v1`、`visual_critic_v1`、`structured_output_repair_v1`。
- [ ] 保留 min 专属 `min_author_*` Prompt，即使其文件版本后缀仍为 `v1`。

### 8.3 Backend 和 Frontend

- [ ] 从 `GenerationMode` 删除 `procedural_v1`，或仅在明确的历史兼容读取类型中保留。
- [ ] 删除 `shader_generation.py` 中的 V1 执行分支和 V1 DTO 映射。
- [ ] 删除 Frontend 的 `procedural_v1` 选择项、说明和运行分支。
- [ ] 删除 V1 fake API 和产品 E2E；先确认相同契约已经由 min E2E 覆盖。
- [ ] 保留读取旧 manifest/Artifact 所需的最小只读适配器，并放入明确的 compatibility 模块。

### 8.4 Node Lab、Memory 和 benchmark

- [ ] 删除 V1 Node Lab Provider 和 V1 descriptor/fixture 的运行入口。
- [ ] 删除或替换 Makefile 中指向 V1 Provider 的 Node Lab 命令。
- [ ] 删除 V1 checkpoint 写入和策略晋升逻辑；历史数据按 Task 5 决策处理。
- [ ] 停止 V1 benchmark 的可执行 Make target。
- [ ] 将 V1 manifest、gate、报告摘要迁入只读 archive；不得删除冻结证据。
- [ ] 可复用参考 PNG 迁入共享或 min benchmark 目录，保留 provenance。

### 8.5 Tests 和打包配置

- [ ] 删除只验证已删除 V1 运行行为的单元和集成测试。
- [ ] 将通用 Renderer、validator、Artifact、Node Lab Harness 测试改名并保留。
- [ ] 更新 `pyproject.toml` package discovery 和 wheel 内容断言。
- [ ] 更新架构边界测试，禁止重新引入 `png_to_shader_v1` 运行模块。
- [ ] 更新 `scripts/docs_check.py` 的 Graph/文档边界。

**验证：**

```bash
make check
uv run pytest -q tests/integration_tests
make test-scene-mvp-ui
make benchmark-scene-mvp-ai-off
make benchmark-node-lab-ai-off
make benchmark-node-lab-model
make test-node-lab-ui
uv run ruff check .
uv run mypy --strict src backend
git diff --check
```

如果 Task 4 或 Task 5 决定正式退役对应能力，应以决策批准的新验收命令替换相关 Node Lab/Memory 命令，不能简单跳过。

---

## Task 9：文档收口和历史兼容确认

**目标：** 当前文档只描述 min 运行事实，历史文档继续能解释 V1 的存在和退役原因。

**修改文件：**

- `README.md`
- `PROGRESS.md`
- `docs/FEATURES.md`
- `docs/ARCHITECTURE.md`
- `docs/DECISIONS.md`
- `src/agent/README.md`
- Agent/Graph/Node/Service/State/ShaderForge 子架构文档
- `backend/README.md`
- `frontend/README.md`
- `docs/NODE_LAB_GUIDE.md`，若 Node Lab 保留
- `docs/evidence/registry.json`

- [ ] 当前架构不再把 V1 表述为注册或默认 Graph。
- [ ] F08/H02 根据实际迁移结果更新状态和验证命令。
- [ ] F09 使用 min 的新 benchmark 与人工证据，不能沿用 V1 passing/failed 结论冒充新门禁。
- [ ] 历史决策保留原文，通过新决策声明 superseded，不回写历史事实。
- [ ] `PROGRESS.md` 记录最终删除范围、历史数据兼容、验证基线和仍未解决缺口。
- [ ] evidence registry 保留 V1 条目并新增退役源码指纹和 min 替代证据。

**最终引用扫描：**

```bash
rg -n "png_to_shader_v1|procedural_v1" \
  src backend frontend tests scripts Makefile langgraph.json pyproject.toml
```

预期：无运行时引用。若保留历史只读兼容，必须限制在明确的 compatibility 模块，并由测试证明不会重新执行 V1 Graph。

历史文档扫描允许有结果：

```bash
rg -n "png_to_shader_v1|procedural_v1" docs benchmarks/archive
```

这些结果必须是历史说明、冻结 manifest 或证据，不得是当前默认运行说明。

## 5. 回滚策略

### G1 前

- 只涉及共享工具搬迁，可按模块恢复旧导入。
- 不允许改变产品默认值或删除文件。

### G4 兼容期

- 保留完整 V1 代码和显式 `procedural_v1` 入口。
- min 出现回归时只回滚默认模式，不覆盖失败 run。
- 已生成的 min Artifact 和 evidence 继续保留。

### G5 后、Task 8 删除前

- 冻结源码指纹或 tag，作为最后可恢复的 V1 可执行快照。
- 核对数据库、Artifact 和外部客户端兼容策略。

### Task 8 后

- 不使用 `git reset --hard` 或批量恢复覆盖当前 min 工作。
- 如确需恢复，只从冻结快照有选择地恢复 V1 feature namespace，并新增回滚决策。

## 6. 风险清单

| 风险 | 后果 | 缓解措施 |
|---|---|---|
| 按名称删除 V1 共享代码 | min Renderer/消息构造失效 | 先完成 G1 解耦和依赖扫描 |
| 未迁移 Node Lab 就删 Provider | H02 和 Lab API 失效 | Task 4 必须先决策并验收 |
| 未处理 Memory 就删 Service | Backend startup/F08 失效 | Task 5 明确迁移或退役 |
| 先删 benchmark 再建立 min gate | 失去唯一质量比较基线 | Task 3 先冻结 min 证据 |
| 同时做新算法和大删除 | 失败原因不可定位 | Task 8 独立增量 |
| 删除历史失败证据 | 无法审计路线切换 | evidence 只增不改，历史归档 |
| 默认切换后立刻删 V1 | 无可用回退 | 先经过 G4 兼容期和 G5 授权 |
| 旧 run 无法读取 | 用户历史结果损坏 | 保留最小只读 compatibility 层 |

## 7. 完成定义

只有同时满足以下条件，V1 清理才算完成：

- `scene_mvp` 是唯一当前生成路径，Backend/Frontend 不再执行 V1 Graph。
- min 不再导入任何 V1 业务命名空间。
- Node Lab 和 Memory 已迁移或有明确、经过验证的退役决策。
- min 的 AI-off、真实模型、人工盲评、浏览器 E2E、生命周期和 Artifact 门禁全部通过。
- V1 Graph、Node、Service、Parser、业务契约和 Prompt 已从运行包删除。
- 历史 V1 run、Artifact、决策、benchmark 和 evidence 仍可追溯。
- `make check`、Integration、min E2E、适用的 Node Lab/Memory 门禁、Ruff、mypy 和 docs-check 全部通过。
- `PROGRESS.md`、`docs/FEATURES.md`、`docs/DECISIONS.md` 和架构文档已经同步最终事实。
