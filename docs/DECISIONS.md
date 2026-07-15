# 决策记录

## D001 - SVG 是最终架构来源

- 日期：2026-07-07
- 决策：`human_doc/shaderforge-technical-architecture-aligned(1).svg` 是权威架构。
- 原因：这是用户指定的最终项目架构。
- 影响：`docs/ARCHITECTURE.md` 只为 agent 可读性总结 SVG；如有冲突，以 SVG 为准。

## D002 - 阶段 1 事实来源保持最小

- 日期：2026-07-07
- 决策：阶段 1 先创建或维护最小事实源：`README.md`、`AGENTS.md`、`Makefile`、`docs/ARCHITECTURE.md`、`docs/DECISIONS.md`、`docs/FEATURES.md`、`PROGRESS.md`，并在前后端职责需要细化时纳入 `frontend/README.md` 和 `backend/README.md`。
- 原因：Learn Harness Engineering 方法要求先建立最小仓库事实来源，再开始功能实现。
- 影响：本阶段只维护事实源和开发规范，不新增产品模块。

## D003 - 暂时保留当前混合目录结构

- 日期：2026-07-07
- 决策：保留 `src/agent/`、顶层 `backend/` 和 `frontend/src/`。
- 原因：当前打包和运行命令已经可用；迁移目录不能提升阶段 1 事实来源质量。
- 影响：顶层应用边界仍保留在 monorepo 内；Agent 与后端内部结构已经分别由 D007 和 D008 规范化。只有当独立发布、独立扩缩容、CI 或导入行为变得痛苦时，再重新评估拆包或拆服务。

## D004 - 采用分层 monorepo 与领域核心包结构

- 日期：2026-07-07
- 决策：后续项目结构采用 `frontend/`、`backend/`、`src/agent/`、`src/shaderforge/` 四层分工。`src/shaderforge/` 用于承载 Routing、Intent IR、DSL、Renderer、Oracle、Search Engine、VLM/HITL、Store 等领域核心能力。
- 原因：最终 SVG 架构包含 UI、HTTP/API、Agent 编排和确定性领域流水线。把所有能力平铺到根目录会碎片化；提前拆成多个服务又会过重。分层 monorepo 能保留清晰边界，同时支持逐步实现。
- 影响：当前不创建空目录。只有当功能进入 `docs/FEATURES.md` 且需要真实实现时，才创建 `src/shaderforge/` 对应子包和测试。

## D005 - 后端启动时执行最小 SQL schema

- 日期：2026-07-07
- 决策：暂时不引入 Alembic 等迁移框架；配置 `DATABASE_URL` 时，后端启动按文件名顺序执行 `backend/sql/*.sql`，创建 Agent 过程数据表。
- 原因：当前只有一份最小 Postgres schema，手写 SQL 和 `asyncpg` 已能满足本地开发与早期验证。迁移框架会增加当前阶段不需要的维护成本。
- 影响：SQL 文件应保持幂等。等出现多环境版本迁移、回滚、数据迁移或部署顺序问题时，再引入正式迁移工具。

## D006 - 后端只调用 Agent 公共接口

- 日期：2026-07-08
- 决策：后端不得直接依赖 Agent 模型工厂、Prompt 加载器或 LangChain 消息类型；后端只通过 `agent.app.services.*` 明确暴露的公共用例服务调用 Agent 能力。
- 原因：后端是 HTTP 边界和编排层，不应该知道具体模型、Prompt 组织方式或 Agent 内部实现。统一接口可以支持后续不同 Agent 实现和 A/B 测试。
- 影响：当前图片到 GLSL 路径通过 `agent.app.services.shader_generation` 暴露结果类型、模型元数据、生成函数、模型调用摘要、业务事件和安全日志摘要；`backend/app/services/shader.py` 只保留薄代理，后端统一写入过程账本。暂不引入注册中心、接口基类或多 Agent 工厂，等出现第二个真实实现后再评估。

## D007 - Agent 内部采用 `agent.app.*` 规范结构

- 日期：2026-07-08
- 决策：`src/agent/` 内部改为 `src/agent/app/` 分层结构，入口按 `config`、`graphs`、`states`、`nodes`、`prompts`、`models`、`services`、`tools`、`observability` 组织。
- 原因：用户希望后续 Agent 侧结构接近独立 agent 项目，但当前仍保持 monorepo 内模块调用，不拆第二个后端服务。
- 影响：不保留 `agent.graph`、`agent.models`、`agent.prompt_loader`、`agent.shader_generation`、`agent.utils.*` 旧导入兼容；`langgraph.json` 注册基础对话图 `src/agent/app/graphs/main_graph.py:graph` 和 Shader 生成/评审图 `src/agent/app/graphs/shader_generation_graph.py:shader_generation_graph`，Prompt 移到 `src/agent/app/prompts/*.yaml`。

## D008 - 后端内部采用 API / Database / Middleware 分层

- 日期：2026-07-08
- 决策：后端保持在 monorepo 根 `pyproject.toml` 下，不拆独立 `backend/pyproject.toml`；内部改为 `backend/app/api/`、`backend/app/database/`、`backend/app/middleware/`、`backend/app/core/`、`backend/app/schemas/`、`backend/app/services/` 分层。
- 原因：用户希望后端目录接近规范 FastAPI 项目；当前功能仍很少，不需要创建 `auth`、`user`、`file` 空包或拆独立后端项目。
- 影响：HTTP route 由 `backend/app/api/router.py` 聚合；健康检查和 Shader 生成分别放在 `backend/app/api/routes/health.py` 与 `backend/app/api/routes/shader.py`；数据库连接与 schema 初始化迁移到 `backend/app/database/session.py`；手写 SQL 记录目录迁移为 `backend/sql/`。

## D009 - Review 节点由前端渲染后单独触发

- 日期：2026-07-08
- 决策：当前在线 Review 节点通过 `POST /api/shader/review` 单独触发。前端先用 WebGL canvas 渲染生成的 GLSL，取第一帧渲染图，再连同原图和 GLSL 发给后端；后端只做 HTTP 校验和 service 编排，Agent 侧在 `nodes/generate_glsl_node.py` 中提供生成节点，在 `nodes/review_render_node.py` 中提供评审节点，并由 `shader_generation_graph.py` 串联调度。
- 原因：当前服务端没有 Renderer，渲染图只存在浏览器 canvas 中；把 review 硬接进 `POST /api/shader/generate` 会缺少用户要求的“当前渲染图”输入。
- 影响：生成接口保持只返回 GLSL；review 暂不写过程数据表。后续如果实现服务端 Renderer 或 ShaderForge Store，再把 review 记录接入谱系、评分和评审账本。

## D010 - 模块说明和边界放在模块旁边的架构文档

- 日期：2026-07-08
- 决策：模块级说明、目录边界和实现规范优先写入模块旁边的 `ARCHITECTURE.md`；模块 `README.md` 只保留入口、运行方式和事实来源索引。Agent 内部子模块也按此规则拆分，例如 `nodes/ARCHITECTURE.md`、`states/ARCHITECTURE.md`、`graphs/ARCHITECTURE.md`。
- 原因：仓库是 agent 的稳定事实来源。开发时 agent 通常会先进入具体目录，子模块旁边的架构文档比集中式长文更容易被新会话及时读取。
- 影响：Agent 端 `src/agent/ARCHITECTURE.md` 收缩为总览和索引；`src/agent/app/` 及其 `config`、`graphs`、`states`、`nodes`、`prompts`、`models`、`services`、`tools`、`observability` 子目录各自维护 `ARCHITECTURE.md`。

## D011 - Agent 模型输出解析独立成 Parser 边界

- 日期：2026-07-08
- 决策：新增 `src/agent/app/parsers/` 保存模型输出纯解析逻辑，例如 GLSL 提取和渲染评审 JSON 解析。
- 原因：`services/` 需要对外暴露稳定解析函数，但不应 import `nodes/` 内部 helper；`nodes/` 负责模型调用，`parsers/` 负责纯解析，可以同时被节点和公共 service 复用。
- 影响：`agent.app.services.shader_generation` 可以 re-export Parser 函数；新增解析器时同步 `src/agent/app/parsers/ARCHITECTURE.md`、测试和 `pyproject.toml` 包配置。

## D012 - Qwen thinking 输出默认不保留

- 日期：2026-07-08
- 决策：DashScope Qwen 的混合思考模式通过 `SHADER_GEN_QWEN_ENABLE_THINKING` 透传到 `extra_body.enable_thinking`；为空时沿用模型默认。`reasoning_content` 默认不保留，只有 `SHADER_GEN_QWEN_OUTPUT_THINKING=true` 时才放入 `AIMessage.additional_kwargs["reasoning_content"]`。
- 原因：thinking 模式会增加延迟和 token 成本，且思维链不应默认进入业务输出、日志摘要或后端响应。当前节点只消费最终 `content`，保留开关主要用于后续调试或受控评估。
- 影响：Qwen 专属配置和 `ChatOpenAI` 变体放在 `src/agent/app/models/qwen_model.py`；DashScope 分支使用 Qwen 专用模型类处理非标准 `reasoning_content` 字段。若后续需要把思维链展示给用户或写入数据库，必须在对应 API 契约和安全策略中单独设计。

## D013 - 模型供应商和模型系列分开解析

- 日期：2026-07-08
- 决策：`llm_factory.py` 先解析供应商前缀，再按真实模型名判断模型系列，不把 provider 和 model family 混为同一层。供应商凭据和 base URL 由 `provider_config.py` 维护；Qwen、GLM、DeepSeek、OpenAI 系列参数分别由 `qwen_model.py`、`glm_model.py`、`deepseek_model.py`、`openai_model.py` 维护。
- 原因：`dashscope` 是供应商，决定 API key 和 base URL；`qwen`、`glm` 等是模型系列，决定 thinking/reasoning 参数和响应字段处理。把二者都塞进一个前缀路由会导致 `dashscope:glm-*` 这类模型错误进入 Qwen 配置。
- 影响：默认模型名仍由 `SHADER_GEN_MODEL_NAME` 控制；推荐写法为 `provider:model`，例如 `dashscope:qwen3.7-plus`。`dashscope:` 不再直接代表 Qwen，真实模型名 `qwen*`、`glm*`、`deepseek*`、`gpt*`/`o*` 决定系列模块。新增供应商时先扩展 `provider_config.py`；新增模型系列时先建独立 `*_model.py` 和测试，再在 `llm_factory.py` 加系列识别。

## D014 - Node 通过 runtime context 配置模型 thinking

- 日期：2026-07-09
- 决策：`models` 向 node 暴露模型无关的调用级语义参数：`model_thinking` 使用 `default/on/off`，`capture_reasoning` 使用布尔值。Node 从 LangGraph `runtime.context` 读取这些配置，再通过 `shader_gen_model()` / `get_provider_model()` 传给模型工厂；模型层负责把语义参数翻译成供应商私有字段。
- 原因：LangGraph 推荐把影响运行但不属于业务 State 的配置放进 runtime context。这样 node 可以按图运行配置覆盖模型行为，同时不直接依赖 DashScope/Qwen 的 `extra_body.enable_thinking` 或 `reasoning_content` 字段。
- 影响：`src/agent/app/models/model_options.py` 保存语义选项和校验；`src/agent/app/nodes/model_runtime_options.py` 适配 runtime context；当前只有 Qwen 系列消费 thinking/reasoning 选项。环境变量仍作为部署默认值，node 级配置只覆盖本次模型实例创建；思维链仍默认不输出，若后续要展示或持久化 reasoning 内容，必须单独设计 API 契约和安全策略。

## D015 - 生成和评审节点保存模型 reasoning_content

- 日期：2026-07-09
- 决策：`generate_glsl_node.py` 和 `review_render_node.py` 显式以 `thinking=on`、`capture_reasoning=true` 调用模型，并从 `AIMessage.additional_kwargs["reasoning_content"]` 提取思维链。思维链通过 `agent.model` logger 打印，同时放入模型调用摘要；后端写入 `agent_events.reasoning_content` 独立字段，不混入 `payload`。
- 原因：这两个节点需要保留模型推理过程用于调试、评估和后续质量分析；独立列比塞进 JSON payload 更容易做权限、清理和查询策略。
- 影响：`agent_events` 新增 `reasoning_content text`，SQL 包含 `ADD COLUMN IF NOT EXISTS` 兼容已有开发库；`POST /api/shader/generate` 和 `POST /api/shader/review` 在数据库连接池可用时，都会把模型调用中的思维链写库。对外 API 响应仍不返回思维链。
- 更新（D016）：本条中"固定以 `thinking=on`、`capture_reasoning=true` 调用模型"的表述已被 D016 取代为节点工厂构造器参数控制；默认值仍与本条一致，存库契约不变。
- 更新（D027）：Legacy 默认 `capture_reasoning=false`、`print_reasoning=false`，只有节点显式 opt-in 才可写入专列；本条的默认采集、打印和存库行为不再适用。

## D016 - 生成和评审节点改为 config 字典工厂控制 thinking

- 日期：2026-07-09
- 决策：`generate_glsl_node.py` 和 `review_render_node.py` 改为节点工厂 `make_generate_glsl_node(config=...)` / `make_review_render_node(config=...)`，`config` 字典（字段 `model`/`thinking`/`capture_reasoning`/`print_reasoning`，风格同 `model_call`）是这两个节点模型与思维链的唯一控制源；图装配时显式调用工厂。`thinking` 控制是否开启模型 thinking（default/on/off），`print_reasoning` 控制是否把思维链打印到 `agent.model` logger，`capture_reasoning` 控制模型是否回吐 `reasoning_content`（打印与存库都依赖它），`model` 写入调用摘要与状态。两个节点不再接收或读取 `runtime` 参数。
- 原因：D015 原实现把这两个节点硬编码为 thinking 常开、思维链常打印，且签名里的 `runtime` 参数被忽略，无法按节点控制是否开启 thinking 或是否打印思维链。改为 config 字典工厂后，模型与思维链配置以 `model_call` 风格的单一字典表达，"是否开 thinking"和"是否打印思维链"成为两个独立、可在装配节点时传入的开关；默认值保持 D015 行为（thinking 开、打印开、存库），生产行为不变。shader 节点与基础对话图解耦：`model_node` 仍走 `runtime.context`，互不影响。
- 影响：`nodes/model_reasoning.py` 删除 `reasoning_model_options()`；新增 `nodes/image_content.py` 抽出两节点复用的图片片段构造；`shader_generation_graph.py` 改为用工厂装配节点；每节点文件顶部声明默认 config 字典常量。`model_calls[*].reasoning_content` 存库契约不变，`backend/` 与 SQL 不动。`Context` / `model_runtime_options.py` 仍由基础对话图使用。
- 更新（D027）：工厂配置能力保留，但 Legacy 两个默认 config 已改为不捕获、不打印 reasoning；显式 `capture_reasoning=true` 的受控调试配置仍可使用专列。

## D017 - Agent 模型层升级为 LLM Gateway

- 日期：2026-07-10
- 决策：删除 `agent.app.models`；由 `agent.app.contracts.llm` 定义中立 Gateway 契约，`agent.app.llms` 提供 LangChain 实现，Graph Builder 显式注入，Node 不直接依赖具体实现层。provider 继续只表示凭据和 base URL，Qwen、GLM、DeepSeek、OpenAI 继续作为 model family 分开适配。
- 原因：统一供应商差异和响应结构，修复节点配置模型与审计模型可能不一致的问题，并支持 Fake Gateway、A/B Graph 和后续供应商扩展。
- 影响：新增 `contracts`、`llms`、`llms/families`、`messages`；reasoning 日志 helper 迁移到 `observability`；后端 API、数据库、前端和 `langgraph.json` 图名不变；不保留旧导入兼容层。D014-D016 中的 `models` 和旧 helper 路径是历史实现，当前路径以本决策和模块架构文档为准。

## D018 - Shader Memory 使用 LangGraph 原生持久化并由 GSSC 构造上下文

- 日期：2026-07-10
- 决策：Shader 生成/评审图使用 LangGraph checkpointer 保存任务内轻量状态，使用 LangGraph Store 保存按 `project_id` 隔离的项目级长期记忆；新增 `agent.app.memory` 和 `agent.app.context`，Context Builder 采用 Gather、Select、Structure、Compress 流程。只借鉴 Hello-Agents 的设计思想，不增加 `hello-agents` 依赖。首期把 `project_id` 同时映射为 `thread_id` 和 Store namespace，`run_id` 继续表示一次 HTTP 调用。
- 原因：项目已经使用 LangGraph，原生 checkpointer/Store 能避免自建 checkpoint、恢复和跨 thread 存储协议；GSSC 可以把当前状态、历史 Review 和项目记忆转换成有预算、可观察的模型输入。当前 State 包含图片字节，必须先区分持久与非持久通道，不能直接保存完整 State。
- 影响：图片、渲染图、完整 GLSL、ContextPack 和本次运行摘要使用 `UntrackedValue`；checkpoint 只保存阶段、迭代、hash 和评审摘要。现有 `Context` 只继续服务基础对话图，不重命名、不扩展到 Shader Memory，Shader Graph 移除无消费者的 `context_schema=Context`。Backend 生命周期创建并关闭 saver/store，通过 Agent 公共 service 注入 Graph；Node 只接触 `Runtime.store`，不接触数据库连接。无 `DATABASE_URL` 时使用明确标记的临时内存模式；PostgreSQL 模式使用官方 persistence 包，框架表由官方 `setup()` 管理。Agent Memory 不替代未来 ShaderForge Store。
- 更新（2026-07-13）：实现前审查进一步明确：`selected_memory_ids`、`memory_status` 与过程摘要属于单次运行，不进入 checkpoint；当前纯 GLSL 输出不创建 `last_generation_summary`；Memory namespace 和记录使用显式 schema 版本；Review upsert 保留 `created_at` 并刷新 `updated_at`，自动 Review 的 importance 使用确定性固定值；Context 优先使用当前 GLSL hash 对应的 Review，避免跨迭代建议冲突。PostgreSQL 首次 `setup()` 使用独立命令，运行时只做健康检查；生产环境启用严格 msgpack 反序列化。

## D019 - PNG 转无贴图 Shader V1 先用受限自由 GLSL 验证闭环

- 日期：2026-07-13
- 决策：F09 V1 使用 `PngToShaderOrchestrator` 确定性主控和 `VisualAnalysisAgent`、`ShaderAuthorAgent`、`VisualCriticAgent` 三个模型角色；V1 先让 Author 生成受 WebGL1 无贴图契约约束的自由 GLSL，通过未来的真实 Renderer、Basic Oracle、`current_best` 和硬预算验证闭环。Effect Genome、参数搜索和独立 `StructureEvolutionAgent` 推迟到 V2。
- 原因：现有项目只有一次生成和一次建议式 Review，最先需要验证的是浏览器渲染事实、局部评分和有限修订能否稳定提高结果。此时同时引入 Genome、搜索器、异步队列和更多 Agent 会扩大调试面，无法判断质量提升来自哪里。
- 影响：M0 创建 `src/shaderforge/contracts/` 作为首个真实 ShaderForge 子包，冻结 `webgl1_static_no_texture_v1`、问题域、停止原因、质量档位、预算和候选接受策略；当前 `image_to_glsl.yaml` 修正为真正禁止参考图采样，并使用 YAML 版本进入模型调用审计。M1 已实现 TargetMeasurements、Validator、真实 WebGL Renderer、Basic Oracle 和本地 Artifact Store；M2 才接三个子 Agent；V1 未完成全部自动化门禁前 F09 保持 active。

## D020 - M1 以独立真实 WebGL1 事实层作为模型闭环的裁判

- 日期：2026-07-13
- 决策：F09 M1 的模型无关事实层统一进入 `src/shaderforge/`。参考图先用 Pillow/NumPy 产生版本化 `TargetMeasurements`；候选先过无贴图 WebGL1 静态 Validator，再由项目自有 Playwright/Chromium worker 在固定 `u_time = 0`、目标分辨率、`antialias = false`、`preserveDrawingBuffer = true` 且不创建或绑定纹理的环境中编译和渲染。Basic Oracle 首期使用 sRGB RMSE/MAE、边缘、主体 bbox/中心/面积、代表像素和 ROI loss；主体 mask 置信度低时衰减几何权重。运行产物通过路径安全、原子写入的 LocalArtifactStore 保存。
- 原因：M2 之后的模型只能提出分析、代码和修订建议，不能同时充当“是否编译、实际画面是什么、质量是否提高”的裁判。先固定可复现的 Renderer、指标和证据格式，才能区分模型问题、Shader 问题、浏览器问题和评分问题。
- 影响：同一 Renderer 生命周期复用 browser/page，但每次 render 新建 canvas/context，编译失败绝不返回上一帧；worker 异常最多重放一次，仍失败则抛出 `RendererUnavailableError`；结果记录 Chromium、WebGL、GLSL、vendor 和 renderer 元数据。M1 只提供可被未来 Graph 调用的公共 API，尚不改变现有 Backend/Frontend 在线路径；D009 的前端 Review 路径在 M4 接入服务端闭环前继续有效。Basic Oracle 不是最终感知指标，线性光、Lab、SSIM、VLM/HITL 和搜索指标留给后续版本按 benchmark 证据演进。

## D021 - M2 采用严格角色契约和最多一次结构化修复

- 日期：2026-07-13
- 决策：VisualAnalysis、三模式 ShaderAuthor 和 VisualCritic 使用版本化 YAML System Prompt、严格 Pydantic 输出契约和纯 Parser。业务语义调用使用部署选择的 `SHADER_GEN_MODEL_NAME`，默认 `temperature=0`、`thinking=on`、`capture_reasoning=true`；合法 JSON fence 在本地解析，JSON/Schema/角色绑定失败时最多追加一次同模型结构修复，并强制 `thinking=off`、`capture_reasoning=false`，第二次失败明确终止。所有 Node 只依赖 `LLMGateway`。
- 原因：Analyst、Author、Critic 的职责和上下文绑定必须可由 Fake Gateway 确定性验证；无限修复或让修复调用重新执行业务推理会放大成本、延迟与语义漂移。compile-repair 还需要防止模型借语法错误大规模重写视觉参数，但纯文本检查不能冒充真实浏览器裁判。
- 影响：Parser 只接受完整 JSON object 或单个 `json` fenced code block，拒绝自然语言包裹、重复 key、非有限数、未知字段、越权 GLSL、版本/mode/candidate/domain 错配；compile-repair scope guard 比较图层、参数 manifest、保护区和真实诊断引用，但最终编译与视觉退化仍留给 M1 Renderer/Oracle。Gateway 分开记录 `requested_model_ref` 与响应元数据优先的实际 `model_ref`；`model_calls` 和 Candidate provenance 记录模型身份来源、Prompt/repair 版本、attempt、usage、输出 hash 与 GLSL hash。M2 不新增 Graph、Backend 或 Frontend 接入，M3 才装配有界循环和 current_best。
- 更新（D024）：真实 M5 canary 后，结构化角色默认的 `thinking=on`、`capture_reasoning=true` 已改为非思考 `json_object`；最多一次修复和严格 Parser 边界保持不变。

## D022 - M3 以 best Artifact 为真相源并由确定性路由控制全部循环

- 日期：2026-07-13
- 决策：F09 M3 新建独立 `png_to_shader_v1_graph`，不扩张既有 `shader_generation_graph`。Graph 负责装配 `prepare_context`、M2 三角色和 M1 事实层；`shaderforge.evaluation.select_current_best()` 是唯一候选晋级入口。候选先写入按 project/run 隔离的 Artifact，再补齐 compile、render、metrics、selection 和 review；Critic、visual-refine 和 finalize 均从 `current_best` Artifact 读取并重新校验 hash，不信任 State 中的 latest GLSL/PNG。只有 hard constraints、Oracle score 和 Selector 都通过的 best 策略可以写入长期 Memory。
- 原因：模型输出、最新候选和最终结果不能同时充当事实与裁判。若 refine 或 repair 失败、退化或模型不可用，仍必须有一个不可被覆盖的历史可运行结果；把 GLSL、PNG、指标和 provenance 绑定为同一候选 Artifact，才能避免旧截图、新源码或错误评分串轮。独立 Graph 也避免 M4 之前影响现有在线 generate/review 路径。
- 影响：模型调用由预算包装器统一计数，剩余一次调用时禁止额外 JSON repair；模型和 Renderer 调用使用 wall-time timeout。compile repair、visual refine、model calls、Shader chars、Renderer replay 受 `BudgetPolicy` 限制，自定义预算不得超过 V1 high 档；Graph 默认 recursion limit 为 96，覆盖 high 档全部有限路径。Renderer 通过 run 级 registry 隔离并在 finalize 关闭。第一个有效候选成为 best，后续候选必须达到 `min_total_improvement` 且 protection regression 不超过阈值，缺失保护证据也拒绝晋级。M3 只提供 Agent/ShaderForge 内部图和 Artifact/Memory 语义；Backend、Frontend、过程账本、Artifact 下载白名单和产品 API 仍属于 M4。

## D023 - M4 复用阻塞 generate，并以白名单 Artifact 和双端渲染复核产品化 V1

- 日期：2026-07-13
- 决策：过渡期继续复用阻塞式 `POST /api/shader/generate`，由 `generation_mode=legacy|procedural_v1` 显式分流；V1 通过独立 `agent.app.services.png_to_shader_v1` 调用 M3 Graph，不把 Graph、Renderer 或 Artifact 路径暴露给 Route。最终结果复制到固定 `final/` 布局，并只开放 `final-render`、`metrics`、`manifest` 三个名字；run_id 到 project_id 使用 LocalArtifactStore 内部持久索引解析。前端用服务端返回的规范化 render 尺寸，以 WebGL1、`u_time=0`、不绑定参考图纹理重新编译最终 GLSL，并对客户端 canvas 与服务端 PNG 计算 RGB RMSE。
- 原因：V1 需要在不引入任务队列、轮询协议或新服务的前提下验证完整产品路径，同时避免客户端提交任意文件路径和前端渲染环境掩盖服务端事实。固定 final Artifact 与 run 索引让 URL 在进程重启后仍可解析；双端实际像素比较比只显示两个画面更能发现分辨率、坐标、WebGL 兼容和运行契约漂移。
- 影响：V1 checkpoint thread 使用 `png-to-shader-v1:{project_id}` 与 legacy 图隔离，但两个图共享同一项目 Store；清除项目记忆同时清除两类 checkpoint/Memory。过程账本在 run input/result JSON 中记录模式、质量、停止原因、current_best 和评分，并把 Graph 累积事件逐项写入 `agent_events`；本阶段仍是完成后一次性落账，不提供实时轮询。D009 继续适用于 legacy 的前端 canvas -> `/review` 路径，`procedural_v1` 不再额外调用 `/review`，而是展示 Graph 最后一次 Critic 结果。异步队列、实时进度、远端对象存储、鉴权下载和保留策略留待实际部署需求出现后设计。

## D024 - M5 使用冻结门禁、非思考 JSON mode 和独立人工盲评

- 日期：2026-07-13
- 决策：F09 M5 使用版本化 10 例 manifest 与 `m5_gate.yaml`，运行前固定 compile/static、initial-final 改善、current_best 单调性、traceability 和 pink-gel 局部阈值。AI-off baseline 只验证 Validator/Renderer/Oracle；AI-on runner 必须显式 `--allow-model-calls` 并受整套调用硬上限。benchmark 单独把 `quality_threshold` 设为 `0` 以确保实际进入 Critic/refine，产品默认早停策略不变。三种结构化角色和 JSON repair 使用 `temperature=0`、`thinking=off`、`capture_reasoning=false`、`response_format=json_object`；原始输出和 reasoning 不落 benchmark，只保留 hash、安全错误码/字段路径、usage、模型/Prompt 身份与候选谱系。WebGL1 无扩展契约同时禁止 `fwidth/dFdx/dFdy`。
- 原因：真实 canary 证明 thinking 模式结构化输出延迟高且会产生不可解析结果；自由文本 JSON 还会让长 GLSL 字符串出现未转义控制字符。供应商 JSON mode 只有在非思考模式下可稳定使用。benchmark 若沿用产品质量阈值，简单样例会在初稿提前停止，无法验证闭环是否真的改善；而把同轮结果用于移动阈值会失去发布门禁意义。自动指标也不能替代人对视觉偏好的判断。
- 影响：`LLMCallOptions` 新增供应商中立 `text | json_object`，各 OpenAI-compatible family 负责请求映射；三个结构化默认配置不影响 legacy 自由文本节点。失败运行和 canary 作为证据永久保留，不覆盖成成功结果。nightly 无条件运行 AI-off，AI-on 只有仓库变量显式开启或手动 workflow 触发时才消耗预算。自动门禁全部通过后仍返回 `pending_human_review`，必须由独立评审者完成 10 项匿名 A/B 并用原运行 config 重新计算，Agent 不得替用户投票。
- 验证结果：正式 run `m5-20260713-balanced-v3` 使用实际审计模型 `dashscope:qwen3.7-plus` 完成 10 例、70 次模型调用。compile/static/traceability/final-current_best/单调性全部通过，但 initial-final 改善率只有 10%，pink-gel 的 bbox、global color 和四个关键 ROI 均失败，因此冻结 gate 为 `failed`，灰度 no-go。人工盲评页已完成浏览器验收但未由 Agent 投票。首次正式 run 暴露 runner 在 dotenv 加载前记录通用 fallback 的问题；逐调用审计未受影响，报告 schema v2 已同时展示旧快照与实际身份，新 config schema v2 在首个模型调用前冻结角色路由和结构化调用参数，旧 schema v1 的不完整 AI-on run 禁止继续。
- 人工复核：2026-07-14 独立评审提交 10 个完整选择，其中 9 个平局、1 个偏好 final；人工完整度通过，final 偏好率 10% 低于 50% 门槛。原始 JSON 按 SHA-256 归档，evaluate 只读取原 config 与 assignments、未调用模型；最终 gate 继续为 `failed`。

## D025 - 结构化小错先做受限本地归一化，失败终态输出安全诊断

- 日期：2026-07-14
- 决策：严格 Parser 本身保持不变。只有 VisualAnalysis 的全部错误都精确指向 `regions_of_interest[*].purpose`，并且原值命中显式、版本化的语义别名表时，`structured_output` 才允许本地改值后重新执行完整 Parser；未知值或任何并存错误仍进入原有最多一次模型修复路径。V1 失败终态由 Agent 公共异常生成安全诊断，Backend 同时打印结构化摘要并写入 `agent_logs` 与失败 run result。
- 原因：真实 422 run `b6fa41c4-a084-4999-8ac3-4600e4990d3b` 中，首次 VisualAnalysis 仅有一个 ROI purpose 枚举错误，模型调用耗时 46.8 秒；同模型重写整份 JSON 又耗时 156.7 秒，导致 300 秒 balanced wall-time 只剩约 97 秒，Author 随后超时且候选数为 0。对已知枚举别名调用大模型重写整份对象既不增加业务语义，也挤占生成候选所需预算。
- 影响：本地归一化记录策略名、修复字段路径和源错误码，但原始模型审计仍标记 `parse_status=invalid`，不会伪装成模型直接输出合法；该路径不适用于缺字段、未知字段、绑定错误、任意枚举或 Author/Critic。后端 `LOG_LEVEL` 现在覆盖 `backend`、`agent`、`shaderforge`，终端可看到阶段、剩余预算、模型耗时、Renderer/评估状态和 finalize；FastAPI route 前置校验的 422 由 `request.validation_failed` 打印安全字段诊断，业务闭环 422 由 `shader.generate.no_validated_result` 打印终态诊断。日志与数据库摘要禁止包含图片、完整 GLSL、reasoning、原始模型响应、用户约束正文或密钥。

## D026 - Metrics Artifact 显式使用 API 形态，HTTP 契约先于成功入账

- 日期：2026-07-14
- 决策：`ScoreBreakdownV1` 继续在领域内以不可变 pair tuple 保存 ROI、保护区和有效权重，但写 metrics Artifact 时必须显式调用 `to_dict()`；Agent service 对旧 pair-list Artifact 做受限兼容归一化。Backend 必须先构造并验证完整 `ShaderResponse`，通过后才批量写事件/日志并把 run 标为 succeeded；响应契约失败则按 backend response 阶段失败入账。
- 原因：真实 run `4cb1b13a-0a10-484a-90a3-c1c392668e0e` 已生成三个候选，第三个通过 WebGL1 且 `total_loss=0.087040`，但 `RunArtifactStore.write_json(score_dataclass)` 经 `asdict()` 把三个映射字段编码为 pair-list，最终 `ShaderScore` 校验失败并返回 HTTP 500。由于旧顺序先写成功账本再构造响应，同一 run 又被错误标记为 succeeded。日志还显示 Graph finalize 后逐条远程写账本额外耗时约 55 秒。
- 影响：新 metrics/final manifest 的映射字段稳定为 JSON object；旧 pair-list score 在 Agent service 边界规范化，不把兼容逻辑扩散到对外 schema。生成成功/失败的事件和日志改为 asyncpg 批量写入，同一次连接租约最后更新 run 状态，减少远程数据库往返；响应构造失败打印 `shader.generate.response_contract_failed` 并返回明确 500，不会再产生新的假成功记录。

## D027 - M6.0 以可恢复结果、类型化失败和安全账本作为发布前可靠性边界

- 日期：2026-07-14
- 决策：在 M5 自动与人工门禁均为 no-go 时，产品默认回退到 `legacy`，`procedural_v1` 只作为明确标注的实验模式。V1 模型阶段分别使用 60/120/60/45/90 秒 cap，并保留总预算 10%、最多 30 秒给确定性修复、Renderer、Evaluator 和 finalize；Legacy 单次生成增加 180 秒服务端 timeout。Initial Author 本地修复只允许在身份字段已经正确时清空两个固定空列表；常量严格倒序 `smoothstep` 只做版本化的确定性意图修复，修后重新执行完整 Validator。Renderer 已成功而 Evaluator 超时或失败时，返回 `unscored_fallback=true`、`score=null`、`metrics_url=null` 的 WebGL-valid GLSL/render，不伪造 `current_best` 或评分。
- 原因：真实 run `80c54e7c-573e-4a3e-b814-48539c77ff53` 在 300 秒内把大量时间花在可本地修复的固定绑定和倒序 `smoothstep` 上，最终以 `wall_time_exhausted` 丢弃了可运行候选。后续审计还发现：评分失败会遮蔽已经通过 WebGL 的 Shader、终态写库可能部分成功、FastAPI 422/供应商/Renderer/内部错误语义混杂、编译日志和 reasoning 原文可能进入普通可观测链路。
- 影响：生成失败统一使用 `{message, code, run_id, stage, retryable, stop_reason}`；wall/model timeout、Renderer、供应商、模型配置/响应、persistence、Shader validation 和内部 pipeline 错误分别映射到稳定 504/503/502/500/422。未知内部异常不再降级成业务 422。`agent_events + agent_logs + agent_runs` 终态写入处于同一事务，先锁 run；同终态重放 no-op，不同终态拒绝覆盖。生成成功后的账本提交失败不得覆盖 HTTP 200，但会记录 `persistence_stage=outcome_commit`。
- 安全更新：Legacy 默认继续允许模型内部 thinking，但 `capture_reasoning=false`、`print_reasoning=false`；只有节点显式 opt-in 才可把 reasoning 写入专列。D015/D016 关于 Legacy 默认采集、打印和存库的历史默认值由本决策取代。普通终端只打印 reasoning 字符数与 SHA-256；WebGL compiler 原文只留私有 compile Artifact，Graph event/数据库只写长度、SHA 和安全错误码。
- 剩余边界：阻塞式 `POST /generate` 和浏览器 AbortController 仍不是真正的服务端任务取消；Evaluator 的 `asyncio.to_thread` 超时后线程可能在后台完成；全 Graph、Store/数据库命令和成功账本尚无统一端到端 deadline/outbox/reaper；过程账本仍按 model calls 后 events 批量排序；项目锁仅单进程有效。以上进入后续可靠性里程碑，不得被本次 M6.0 的测试通过误写成已解决。

## D028 - F09 Node Lab 使用调试专用 Adapter 和不可变步骤快照

- 日期：2026-07-14
- 决策：把 PNG-to-Shader V1 的节点教学、隔离测试和故障定位设计为默认关闭的 `/api/lab/v1/*` 调试边界，并继续归属唯一 active 功能 F09。一个 allowlist Node Registry 通过通用步骤接口覆盖生产图全部 19 个节点；Validator、Renderer、Oracle、Selector 和路由另提供复用同一公共能力的友好接口。Node Lab 不直接暴露 `PngToShaderV1State`，而是为每一步保存不可变 JSON-safe 快照，把图片、GLSL、渲染图和原始模型内容转换为 Lab Artifact 引用，再由显式 Adapter 重建节点内部类型。模型步骤支持 preview、fixture、mock 和双重显式开关保护的 real 模式；V1.0 的策略 Memory 晋升只 preview，不写真实项目 Memory。
- 原因：V1 的大对象和证据使用 `UntrackedValue`，不能把 LangGraph checkpoint 误当成可跨请求完整恢复的真相源；Backend 也不能为了调试直接 import Node、Prompt、Gateway 或领域算法。不可变 `base_step_id` 快照既能支持重试和分支，又不会覆盖产品 run、current_best 或失败证据。独立 Validator/Renderer/Oracle 接口可以把契约 Bug、基础设施故障、正常候选拒绝和视觉质量不足分开观察，同时避免为每个内部 helper 建立长期 HTTP 契约。
- 影响：完整设计记录在 `docs/superpowers/specs/2026-07-14-node-lab-api-v1-design.md`。阶段 A 已创建 transport-free Schema/error、19 节点 Registry、Fixture Registry、Application API、LabRun/不可变步骤/Artifact Store 和聚焦测试；未知节点不反射执行，Artifact 只通过同一 LabRun 的不透明 id 读取，Executor 失败保留安全证据但不泄漏原始异常。Node Lab 继续保持默认关闭、禁止 reasoning/密钥/供应商原始异常外泄，也不改变产品 API 和 F09 no-go 结论。
- 更新（2026-07-14）：Node Lab 不只服务人工 Swagger 调试，而是人工、Agent/Codex 自动化、模块化测试和 benchmark 共用的 Harness。`agent.app.services.node_lab` 的 Python Application API 是唯一执行真相源，HTTP 与 CLI 只做 transport；三者共用 Registry、Adapter、Fixture、请求/响应和 Artifact 语义。新增版本化 batch manifest、AI-off/显式 AI-on runner、逐 attempt 证据、source/environment fingerprint、cold/warm Renderer profile、冻结 gate 和 report comparison 设计。现有 M5 固定 10 例、suite 输出、冻结 gate 与人工盲评继续独立，Node Lab 模块 benchmark 不经 FastAPI 运行，也不能替代或覆盖 M5 发布证据。
- 更新（2026-07-14，阶段 B.1）：已接通八个确定性 capability、五个生产节点 Adapter、冻结 manifest、AI-off CLI/report/comparison，以及默认关闭的 HTTP/Swagger 包装。Renderer capability 每次独立创建并关闭真实 Chromium 生命周期，因此当前只声明 `renderer_cold`；warm、scenario/transport benchmark、HTTP batch、模型路径和其余节点仍后置，descriptor 必须继续明确 `available | partial | planned`，不得把设计目标报告成已实现能力。
- 更新（2026-07-14，阶段 B.2）：确定性 benchmark 增加五步 scenario、独立 `renderer_warm` suite 和 direct-vs-HTTP transport profile。Warm suite 在一次 suite 内复用 Renderer，至少一次 warmup 且与 cold 数据分开报告；恢复 warm suite 时先追加不计入统计的 rewarmup。取消或键盘中断会先写独立 interruption 证据，恢复只补缺失的 `execution.json`，中断仍保留在失败分母，禁止用恢复覆盖事故现场。HTTP batch 只接受三个仓库内固定 AI-off suite id，同步返回机器可读报告；不接受 manifest 路径、real 模式或模型开关。阶段 B 至此完成，但模型路径、其余 14 个节点和每节点 Fixture 仍属阶段 C，F09 的 M5 no-go 不变。
- 更新（2026-07-14，阶段 C）：19 个生产图节点已全部通过 exact `(node_id, execution_mode)` Executor 接通，descriptor 统一为 `available`。九个新增确定性 Adapter 负责初始化、只读 Context、Artifact/候选状态迁移、best 重载、Review 持久化、finalize 与策略晋升 preview；已有 render/select Adapter 使用生产 CandidateRecord、hash/evidence binding 和单调 best 语义。五个模型节点复用生产 Prompt、消息构造、严格 Parser、结构化修复和有界模型包装，支持无调用 preview、版本化 fixture、自定义 mock 和 real。real 在创建 step 前同时检查服务端开关、请求开关与 Gateway；`project_commit` 同样在副作用前 fail closed，Node Lab 不写真实 Memory。
- 更新（2026-07-14，模型 benchmark 与阶段 D 边界）：新增独立五角色 runner，默认 fixture 离线；real 必须显式 CLI flag、环境开关和 Gateway，并冻结参考图、Fixture、Prompt/模型参数及 semantic/repair/token/wall/cost 预算。逐 attempt 原子证据、失败和中断分母、JSON/Markdown 报告独立于 M5。当前不让 M5 runner 改走 Node Lab，也不创建可选前端页面：前者避免改变已冻结发布证据语义，后者在 Swagger、Python API 和 CLI 已满足教学/自动化需求时继续后置。
- 更新（2026-07-15，阶段 D 完成）：保留“不让 M5 runner 改走 Node Lab”的发布边界，但撤销“前端继续后置”的当时取舍，完成只消费 descriptor/HTTP 的 `/lab` 本地工作台。Stage D 同时补齐此前审计出的实际缺口：19 节点机器可读输入示例和五模型 Parser 拒绝 Fixture；直连公共 Application API 的逐节点 CLI；HTTP/OpenAPI 命名示例、步骤 DAG 摘要和 Artifact descriptor 列表；deterministic runner 的真实 node target 与 `base_step_id` pipeline；模型 runner 的中断恢复、累计预算、provider `max_output_tokens`、requested/actual model 与价格版本冻结、五角色 Parser/Schema/binding/timeout/latency/token/cost 聚合，以及样本不足时不报告 p95。三类 benchmark CLI 统一为稳定单行摘要和 `0/1/2/3/130` 退出码，case 失败即非零，不再依赖可选 `--require-passed` 才失败。V1.0 不新增 `/contracts/*` 或 `/roles/*` 第二套 HTTP 别名：node/capability descriptor 是契约目录，五模型角色统一走通用 step，避免 Schema、预算和错误语义分叉。M5 report、gate、人工评审与 output root 继续互不消费，Node Lab 完成仍不改变 F09 no-go。
- 更新（2026-07-15，生产图扩展）：D030 新增 `prepare_measurement_seed` 后，Node Registry 与精确 Executor 同步扩为 20/20：15 个 deterministic、五个模型节点。Node Lab 复用生产 generation API，把 seed 的完整 GLSL/Author/provenance 留在私有 Artifact，并在 `materialize_candidate` 校验 independent root、origin、generator version 和 hash 绑定；API、CLI、工作台、模块 benchmark 与文档均从 descriptor 获取新节点。该扩展不改变默认关闭、Memory 只 preview、M5 发布证据独立和真实模型双开关边界。

## D029 - M5 改善门禁使用 manifest 同口径 pair 并区分候选来源

- 日期：2026-07-15
- 决策：新 M5 run 使用 config/report schema v3。gate 的 `initial_total_loss` 与 `final_total_loss` 都由 runner 针对冻结 PNG、manifest `key_rois` 和同一个 `manifest_key_rois_v1` objective 独立重算；生产 Selector 的内部 loss 只以独立字段保留诊断。bbox 直接以 manifest `expected_foreground_bbox_uv` 为期望。initial 固定为按候选顺序首个成功的 `origin=model` 记录；`origin=deterministic` 的候选可成为 final，但必须保存 `generator_version`，不得同时冒充 initial。旧记录缺 `origin` 时按模型兼容；没有成功模型 initial 时该 case 不可比较，也不生成盲评包。
- 原因：旧 runner 把生产动态 ROI 下的初始分数与 manifest ROI 下重算的 final 分数放进同一个改善 gate，比较口径不一致；自动测量参考图 bbox 作为期望也绕过了 manifest 已冻结的几何契约。确定性 seed 加入候选池后，如果仍按首个成功候选选 initial，还可能让 seed 同时成为 initial/final，制造无意义的自动改善与人工平局。
- 影响：冻结阈值、10 例 manifest 和历史正式输出均不修改。旧 schema v1/v2 完整运行仍可只读查看原结论，但不完整 AI-on 禁止续接到 schema v3；新运行必须使用新的 suite run。报告和 traceability 明确展示每个 initial/final 的 origin 与 generator version，人工盲评只在全部 case 都存在有效 model initial/final 图对时生成。

## D030 - 生产候选池加入一次性 measurement affine 独立根候选

- 日期：2026-07-15
- 决策：在首个成功 model 候选经过事实层和 Selector 后，生产 `png_to_shader_v1_graph` 至多插入一次 `measurement_affine_seed_v1`。该候选只消费规范化 RGB PNG 与 `TargetMeasurements`，在主体 bbox 局部坐标拟合前景 RGB affine plane，低置信或拟合不可用时回退 palette solid ellipse；它是 `parent_candidate_id=null` 的独立根，明确记录 `origin=deterministic` 和 generator version。seed 不消耗模型、compile-repair 或视觉迭代预算，仍完整经过 Validator、真实 WebGL1 Renderer、Oracle 和 Selector；失败或退化时保留既有 model best，且不增加模型 stagnation。生产评分同时以追加方式合并严格 `VisualAnalysis` 语义 ROI，Critic Review 改为按轮次保存。
- 原因：旧正式 M5 中 9/10 的 initial/final PNG bit-identical，说明仅依赖模型 Critic/refine 没有产生稳定搜索多样性；同时生产 Oracle 只看到通用测量 ROI，无法把 Analyst 已确认的高光、阴影和颜色区域反馈给 Selector。一个不读取 case id、manifest、golden 或 gate 的通用确定性 seed，可以提供可复现的第二个候选和可靠 fallback，但不能自行决定是否更好。
- 影响：`src/shaderforge/generation` 成为新的确定性领域边界，并通过 `shaderforge.public` 暴露；Graph 生产节点由 19 增至 20，Node Lab 同步暴露该节点。CandidateRecord 和 M5 traceability 收紧候选来源/version 证据。固定 10 例真实 Chromium 离线回归和 pink-gel 冻结局部阈值已通过，但这不等于新的真实模型自动门禁或独立人工偏好门禁通过；F09 在新 AI-on run 与新人工评审完成前继续保持 active/no-go。

## D031 - 人工盲评公开包与私有映射隔离并封存完整证据链

- 日期：2026-07-15
- 决策：新 M5 run 在 config 中冻结独立 `blind_review_evidence_schema`，只把 `blind-review/reviewer/` 作为评审者可见目录，`assignments.private.json` 留在父目录；evidence manifest 对 initial/final/reference source、公开 A/B assets、页面、template 和私有映射记录 byte size 与 SHA-256，首次 `report.json` 再锚定 manifest SHA。evaluate 必须在读取人工 JSON 和覆盖报告前复验 config/report 锚点、manifest 与逐文件内容。没有该 schema 标记的历史 run 不原地迁移、不事后补签，改用稳定 A/B 算法和冻结 v1 页面/template 对 source/assets 做确定性只读复验。
- 原因：旧包虽然没有在 HTML 中直接写 initial/final 身份，但页面、assets 与 `assignments.private.json` 位于同一可分发目录，流程上的 “private” 仍可能被误共享；只校验 manifest 内的自述哈希也无法阻止同时修改 manifest 与文件。公开/私有目录隔离和首次报告的二级锚点能同时降低身份泄露与评审期间产物漂移风险，又不能以安全加固为由改写已经冻结的正式结果。
- 影响：冻结的 10 例、质量 objective、自动/人工阈值和 schema v1 人工选择契约均不改变，也不产生任何模型调用。历史 run 只能向评审者单独导出旧 `index.html + assets/`；导出物不参与 gate，原 suite 仍由 legacy verifier 读取。新 run 的 report 明确给出 reviewer path、manifest path/hash；没有实际盲评包的 AI-off report 不得宣称存在 reviewer 页面。

## D032 - Node 是 Node Lab 节点语义的唯一实现

- 日期：2026-07-15
- 决策：Node Lab 保持 transport-free Harness 与生产 Graph 解耦，但不再为生产 Node 平行实现状态转换。15 个非模型 descriptor 由 `DeterministicNodeExecutor` 把 JSON-safe State 和不透明 Lab Artifact 适配成生产输入，随后直接调用 Graph 使用的 Node factory 或纯 routing；五个模型 descriptor 继续调用生产角色 Node factory 和 bounded wrapper。`lab/adapters.py` 只保留可独立 benchmark 的 ShaderForge/routing capability。初始化、Candidate 物化、measurement seed 绑定、render/evaluate、selection、best 重载、Review 持久化、finalize 和策略晋升 preview 的证据规则归生产 Node 所有。
- 原因：旧 Node Lab 的 initialize、materialize、render/select、load/finalize 和 promotion preview 与生产 Node 分别维护相似逻辑。生产规则变化时，Lab 的平行实现可能继续通过，从而验证错误语义；阶段名 `StageCDeterministicExecutor` 也把一次性开发阶段固化进长期类型。Harness 应测试 Node 的公开 callable 契约，而不是成为第二套 Node。
- 影响：`StageCDeterministicExecutor` 更名并重写为长期职责名 `DeterministicNodeExecutor`，覆盖全部 15 个 deterministic 节点；生产 Node 增加 Author/provenance/GLSL、Candidate/hash 和 reference/measurements 绑定校验，并暴露无副作用的策略晋升 preview Node。Lab ArtifactStore facade 把生产逻辑 ref 映射为不透明 Artifact id，原始 compiler/console 仍只在私有 Artifact，响应只保留安全摘要。`agent.app.lab` 继续不依赖 `agent.app.nodes`、Backend、FastAPI 或具体 Gateway，Node 装配只发生在 `agent.app.services.node_lab`。本次不改变 Graph 节点、边、路由结果、M5 gate 或真实模型调用边界。
- 更新（2026-07-15，审查加固）：生产 `materialize_candidate` 进一步校验 model 候选的 mode/author version/prompt version、root 或 current_best 绑定，并拒绝 model 携带 deterministic generator version；策略晋升 real/preview 在读取 Author 前重新校验 final 的 project/run/candidate/GLSL/render/score 与 current_best 一致。Node Lab 单步 `render_and_evaluate` 继续执行同一生产 Node，但使用 Service 注入的单步 Renderer registry，在节点完成或失败时关闭资源，并按实际 factory 调用记录 browser launch；生产 Graph 仍保持 run 级复用和 finalize 关闭，不改变线上语义。
- 更新（2026-07-15，Provider 解耦）：Node Lab 内核不再持有 PNG-to-Shader 的 20 节点 descriptor、`SUPPORTED_NODE_IDS`、逐节点 dispatch 或具体 Node/Graph import。新增通用 `NodeProvider` / `NodeExecutorBinding` / `NodeExecutionHost` 协议，pipeline id、descriptor、执行模式、routing capability、Adapter 和 benchmark 生产源文件均由 `agent.app.nodes.integrations.node_lab` 暴露。`NodeLabApplication` 只自动安装 Provider binding，并校验 descriptor pipeline 一致性、模式覆盖和 LabRun pipeline 隔离。普通 JSON-safe Node 可以用 `DirectNodeExecutor` 零适配接入；有 Artifact/Renderer/Memory/模型依赖的 Node 只在生产 Provider 内写专用 Adapter。因此新 Node 不再修改 Lab 内核或 Service，但仍必须在生产侧明确声明可调试契约；禁止为了“零登记”而反射 import 客户端字符串。

## D033 - 自动 objective 通过不能覆盖独立人工偏好失败

- 日期：2026-07-15
- 决策：正式 run `m5-20260715T023445Z` 的自动检查 12/12 通过，但独立盲评 final 偏好率只有 30%，低于冻结的 50% 门槛，因此最终 gate 必须保持 `failed`，F09 继续 active/no-go。不得因为自动 objective 通过而移动人工阈值、重解释平局、选择性排除 case，或把当前 run 写成已通过；该 run 只作为 M6.2 的诊断基线，修复后必须使用新 suite-run-id、完整硬预算和新一轮独立盲评。
- 原因：自动 gate 证明 8/10 case 在 `manifest_key_rois_v1` 下改善，并验证 compile/static、traceability、current_best 与局部阈值，却不能证明人类偏好的结构、实例数量和高光/阴影语义得到保留。人工解码为 final/initial/tie `3/4/3`；`rimmed_disk`、`arc_highlight_orb`、`dual_disks`、`pink_gel` 均偏好 initial，说明低频 affine 近似可能降低像素 objective，同时损伤视觉拓扑和语义层次。
- 影响：M6.2 优先在 Node Lab 增加结构与语义诊断，检查 topology、实例数量、轮廓/镂空、高光/阴影以及 Selector 的结构保护证据；不得加入 benchmark case id、golden、manifest 或 gate 特判。完成离线回归后，仍需重新执行真实模型 M5 和独立人工门禁；当前失败评审 JSON、报告与逐 case 产物继续只增不改保存。
