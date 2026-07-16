# 7.10 阶段总结（历史快照）

> 截止日期：2026-07-10。本文件仅用于审计与追溯，不代表当前架构、功能状态、契约、质量门禁或验证基线。当前权威入口：项目概览与文档导航见根目录 `README.md`，协作约束见根目录 `AGENTS.md`，当前状态见根目录 `PROGRESS.md`，功能状态见 `docs/FEATURES.md`，架构与决策分别见 `docs/ARCHITECTURE.md`、`docs/DECISIONS.md`。

本阶段工作主要围绕 ShaderGen 的最小可运行闭环、仓库工程规范和 Agent 架构边界进行梳理与落地。

当前项目还没有直接进入完整的 ShaderForge 生成、渲染、评分和搜索流水线，而是先完成了可验证的基础工程能力：明确最终架构来源，建立 Agent、后端、前端之间的职责边界，并实现“图片生成 GLSL → 浏览器渲染 → 在线 Review”的最小闭环。

---

## 主要工作内容

### 1. 建立仓库事实来源和功能状态管理

首先对项目中的架构说明、功能状态、决策记录和开发入口进行了统一。

当前以 `human_doc/shaderforge-technical-architecture-aligned(1).svg` 作为最终技术架构来源，仓库中的 `README.md`、`AGENTS.md`、`docs/ARCHITECTURE.md`、`docs/FEATURES.md`、`docs/DECISIONS.md` 和 `PROGRESS.md` 分别承担入口说明、开发约束、架构边界、功能状态、关键决策和当前进度等职责。

同时对功能状态加入了最小约束：

- 同一时间最多只有一个 `active` 功能；
- 功能只有通过对应验证后才能标记为 `passing`；
- 已完成能力必须保留验证证据；
- 尚未实现的后续能力不能提前以目录或空模块的形式存在。

这样做的目的，是避免项目在早期就因为“目标架构很大”而出现大量空目录、空接口和无法验证的规划性代码。

当前 `H01`（新 Agent 会话可通过仓库文件理解项目）和 `F06`（在线 Review 节点）已经通过验证；用户任务输入、Intent IR、DSL Renderer、Oracle、Search Engine 等仍明确保留为后续功能项。

---

### 2. 收敛前端、后端、Agent 和领域核心的分层边界

本阶段明确了项目采用分层 monorepo 的方式演进：

- `frontend/` 负责用户输入、图片预览、WebGL 渲染和结果展示；
- `backend/` 负责 FastAPI HTTP 边界、请求校验、错误处理和后端 service 编排；
- `src/agent/` 负责 LangGraph 图、模型调用、Prompt、节点、解析和 Agent 对外公共服务；
- `src/shaderforge/` 预留给后续确定性领域能力，例如 Intent IR、DSL、Renderer、Oracle、Search、Review 和 Store。

其中，`src/shaderforge/` 目前没有为了“看起来完整”而提前创建空包。只有当具体功能进入实现阶段时，才会创建对应的领域模块和单元测试。

后端与 Agent 的调用关系也进行了收敛：

- 后端只能通过 `agent.app.services.*` 调用 Agent 能力；
- 后端不直接依赖 Prompt、模型工厂、LangChain 消息类型或 Agent 内部节点；
- HTTP route、schema、service、SQL 和数据库连接分别放在明确目录中；
- Agent 内部模块说明优先放在模块旁边的 `ARCHITECTURE.md` 中，减少后续会话重新理解目录结构的成本。

这一轮工作主要是在解决“当前代码能运行”和“后续项目可以继续增长”之间的边界问题。

---

### 3. 实现图片生成 GLSL 与在线 Review 的最小闭环

当前已经完成最小的 Shader 生成与 Review 流程。

生成路径中，前端提交图片，后端通过 Shader service 调用 Agent 公共服务，Agent 使用图像到 GLSL 的 Prompt 和模型调用生成 GLSL，前端再使用 WebGL 对生成结果进行预览。

Review 路径中，浏览器在 WebGL canvas 完成首帧渲染后，获取当前渲染图，并将：

- 原始参考图；
- 当前 canvas 渲染图；
- 当前 GLSL；

发送到 `POST /api/shader/review`。Agent 根据图像差异和代码内容输出渲染评估以及代码修改建议，前端负责展示结果。

这里没有把 Review 强行塞进生成接口，是因为当前渲染结果存在于浏览器 canvas 中，服务端还没有独立 Renderer。生成接口只解决 GLSL 生成，Review 接口只在拿到真实渲染图后执行，职责相对清晰。

Agent 内部也将生成和评审拆为独立节点：

- `generate_glsl_node` 负责图片到 GLSL；
- `review_render_node` 负责原图、渲染图和 GLSL 的评审；
- `shader_generation_graph` 负责图的编排；
- `parsers/` 负责 GLSL 和 Review JSON 的纯解析逻辑；
- `services/` 对后端暴露稳定的公共用例入口。

这样可以避免节点、解析、模型调用和 HTTP 编排全部混在同一个文件中。

---

### 4. 梳理模型供应商、模型系列和 thinking 配置

对模型配置进行了进一步拆分。

之前容易把 `dashscope`、`qwen`、`glm`、`deepseek`、`openai` 等概念混在同一层：其中有的是供应商，有的是模型系列。现在先解析 `provider:model` 中的供应商前缀，再根据真实模型名识别模型系列。

例如：

- `dashscope` 决定 API Key 和 Base URL；
- `qwen`、`glm`、`deepseek`、`gpt` 等决定模型参数、thinking 行为和响应字段处理；
- `dashscope:glm-*` 不会再因为使用了 DashScope 凭据而被错误当成 Qwen 模型处理。

同时，thinking 和 reasoning 内容也进行了配置化处理：

- thinking 可以按 `default/on/off` 控制；
- 是否回收 `reasoning_content` 可以独立控制；
- Shader 生成和 Review 节点通过配置工厂决定模型、thinking、reasoning 捕获和日志输出；
- reasoning 内容默认不直接返回给前端，而是作为模型调用摘要和过程数据的一部分，用于调试、评估和后续分析。

这部分工作主要是为了降低不同模型供应商接入时的耦合，也避免将模型私有参数泄漏到业务节点和后端接口中。

---

### 5. 完善 Agent 过程数据和最小可观测能力

当前 Agent 不只返回最终 GLSL 或 Review 结果，也会在公共服务边界返回结构化过程摘要。

主要包括：

- 模型调用摘要；
- 业务事件；
- 安全日志摘要；
- 必要时的 reasoning 内容。

后端在数据库可用时，将这些过程数据写入 `agent_runs`、`agent_events` 和 `agent_logs` 等记录中。reasoning 内容单独存放，不与普通 JSON payload 混在一起，方便后续做查询、权限、清理和保留策略。

目前这套过程记录还是最小版本，重点不是马上搭建复杂的观测平台，而是保证后续引入 Intent、Renderer、Oracle、Search 等能力后，能够回溯一次生成过程里发生了什么。

---

### 6. 增加文档边界检查和验证约束

为了避免架构说明与实际代码逐渐失效，本阶段增加了 `docs-check` 和对应的契约测试。

检查重点包括：

- 功能状态是否符合状态机规则；
- `passing` 功能是否保留验证证据；
- Agent README 是否仍能作为模块入口；
- 后端是否越过公共 service 边界直接依赖 Agent 内部实现；
- LangGraph State 的过程数据是否使用 append reducer；
- 文档中的架构和路径约束是否出现明显漂移。

当前已完成的验证覆盖：

- Python 单元测试；
- 后端与 Agent 的集成测试；
- `docs-check`；
- LangGraph graph 校验；
- 前端构建；
- Python lint 检查。

其中，LangGraph 当前能够发现基础对话图和 Shader 生成/评审图两个 graph。真实模型调用没有作为单元测试依赖，测试主要使用模拟模型和接口契约，避免验证过程依赖密钥、网络状态和模型随机性。

---

## 测试问题与思考

当前项目已经有最小的图片生成 GLSL 和在线 Review 能力，但离最终 ShaderForge 架构仍有明显距离。

首先，当前 Review 更接近“基于原图、渲染图和 GLSL 的模型评审”，还不是稳定的视觉评测系统。浏览器端通过 canvas 首帧截图完成 Review 输入，尚未补齐完整的浏览器端自动化闭环；`F07` 仍需要 Playwright 或等价的端到端检查来验证截图、接口失败和 UI 展示行为。

其次，当前尚未形成确定性的图像评估能力。后续的 Oracle 不应只依赖模型主观判断，还需要逐步引入全局差异、颜色、形状、边缘等局部损失，才能为 Search Engine 提供相对稳定的优化信号。否则，即使生成和评审 Agent 都能工作，迭代方向仍可能受模型输出波动影响。

模型 thinking 和 reasoning 的保留也需要继续观察。当前保留 reasoning 有利于调试和评估，但会带来延迟、token 成本、日志体积和数据治理问题。后续应进一步明确哪些阶段需要 reasoning、哪些场景只保留最终结果，以及 reasoning 的截断、脱敏、权限和保留周期。

接下来更合理的推进顺序是：

1. 实现用户任务输入与测试规划输入；
2. 将自然语言需求整理为结构化 Intent IR；
3. 建立 DSL、Renderer 和可复现的 GLSL 生成路径；
4. 引入 Oracle 作为确定性视觉评价基础；
5. 在此基础上实现 Search、VLM/HITL Review 和过程谱系记录。

当前阶段完成的重点不是把最终架构全部铺开，而是先保证后续每增加一个能力，都有明确入口、清晰边界、可验证状态和可追溯过程。
