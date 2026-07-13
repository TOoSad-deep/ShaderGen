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

## D016 - 生成和评审节点改为 config 字典工厂控制 thinking

- 日期：2026-07-09
- 决策：`generate_glsl_node.py` 和 `review_render_node.py` 改为节点工厂 `make_generate_glsl_node(config=...)` / `make_review_render_node(config=...)`，`config` 字典（字段 `model`/`thinking`/`capture_reasoning`/`print_reasoning`，风格同 `model_call`）是这两个节点模型与思维链的唯一控制源；图装配时显式调用工厂。`thinking` 控制是否开启模型 thinking（default/on/off），`print_reasoning` 控制是否把思维链打印到 `agent.model` logger，`capture_reasoning` 控制模型是否回吐 `reasoning_content`（打印与存库都依赖它），`model` 写入调用摘要与状态。两个节点不再接收或读取 `runtime` 参数。
- 原因：D015 原实现把这两个节点硬编码为 thinking 常开、思维链常打印，且签名里的 `runtime` 参数被忽略，无法按节点控制是否开启 thinking 或是否打印思维链。改为 config 字典工厂后，模型与思维链配置以 `model_call` 风格的单一字典表达，"是否开 thinking"和"是否打印思维链"成为两个独立、可在装配节点时传入的开关；默认值保持 D015 行为（thinking 开、打印开、存库），生产行为不变。shader 节点与基础对话图解耦：`model_node` 仍走 `runtime.context`，互不影响。
- 影响：`nodes/model_reasoning.py` 删除 `reasoning_model_options()`；新增 `nodes/image_content.py` 抽出两节点复用的图片片段构造；`shader_generation_graph.py` 改为用工厂装配节点；每节点文件顶部声明默认 config 字典常量。`model_calls[*].reasoning_content` 存库契约不变，`backend/` 与 SQL 不动。`Context` / `model_runtime_options.py` 仍由基础对话图使用。

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
- 影响：M0 创建 `src/shaderforge/contracts/` 作为首个真实 ShaderForge 子包，冻结 `webgl1_static_no_texture_v1`、问题域、停止原因、质量档位、预算和候选接受策略；当前 `image_to_glsl.yaml` 修正为真正禁止参考图采样，并使用 YAML 版本进入模型调用审计。M1 先实现 Validator、真实 WebGL Renderer、Basic Oracle 和本地 Artifact Store；M2 才接三个子 Agent；V1 未完成全部自动化门禁前 F09 保持 active。
