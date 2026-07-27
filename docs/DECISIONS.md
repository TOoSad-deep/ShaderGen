# 决策记录

> 本文件保存不可丢失的历史取舍，不是当前实现说明。当前架构、功能状态和交接事实分别以 `docs/ARCHITECTURE.md`、模块旁 `ARCHITECTURE.md`、`docs/FEATURES.md` 和 `PROGRESS.md` 为准。除下表明确标注外，其余决策默认为 `accepted`。

## ADR 状态索引

状态含义：`accepted` 表示当前仍适用；`updated` 表示核心取舍仍适用、正文中的路径或局部实现已由后续决策更新；`superseded` 表示只保留历史审计价值，不得作为当前契约。

| 决策 | 状态 | 后续决策 | 当前解释 |
|---|---|---|---|
| D001 | superseded | D050 | SVG 降为最初设计参考，不再是当前架构的覆盖性权威。 |
| D002 | superseded | D010、D037 | 阶段 1 的最小事实源已演进为模块就近文档和有界交接。 |
| D005 | updated | D043 | 启动时执行幂等业务 SQL 的原则仍有效，SQL 现为显式 wheel 资源包。 |
| D006 | updated | D035、D036 | Backend 只调用 Agent 公共 service 的边界仍有效，旧 service 路径已删除。 |
| D007 | updated | D017、D036、D038 | `agent.app.*` 结构仍有效，旧 Graph、models 层和 Node 路径仅属历史。 |
| D009 | superseded | D036 | 独立 `/api/shader/review` 与浏览器先渲染流程已删除。 |
| D011 | updated | D036 | Parser 纯边界仍有效，旧 Shader generation service 已删除。 |
| D012 | updated | D017、D027 | reasoning 默认不进入业务输出的原则仍有效，当前实现位于 LLM Gateway。 |
| D013 | updated | D017 | provider/model-family 分离仍有效，旧 models 路径已迁入 LLM Gateway。 |
| D014 | superseded | D017、D036 | 旧 runtime-context 模型配置随旧基础图和 models 层删除。 |
| D015 | superseded | D016、D027、D036 | 默认采集、打印和写入 reasoning 的行为已取消。 |
| D016 | superseded | D017、D027、D036 | 旧生成/评审 Node 工厂及其 config 已删除。 |
| D018 | updated | D036、D044、D068 | Memory/checkpoint 实现和数据保留，但当前产品生命周期不再接入。 |
| D023 | superseded | D036、D066 | V1 产品路径、Artifact 与项目 Memory API 已删除。 |
| D027 | updated | D036 | 可靠性与安全账本原则仍有效，legacy 兼容部分已删除。 |
| D028 | superseded | D032、D038、D065 | 旧 Node Lab 已退役，正文只保留历史审计价值。 |
| D032 | superseded | D038、D065 | 旧 Node Lab Provider 已退役，正文只保留历史审计价值。 |
| D034 | updated | D038 | 按职责拆分仍有效，文件已进一步迁入 V1 功能命名空间。 |
| D035 | updated | D036、D044 | 薄 Route、Backend Service 和 Renderer 双层清理仍有效；Backend persistence 清理由 D044 加固。 |
| D046 | updated | D065 | 统一前端 API Client 原则仍有效，旧 Node Lab 明细加载部分已退役。 |
| D062 | accepted | — | `scene_mvp` 新增仅限独立实验的 manual 预算档。 |
| D063 | accepted | — | 不接入多尺度 tile 最大回退 guard 的离线 replay 形式。 |
| D064 | updated | D065 | 共享依赖解耦仍有效；旧 Node Lab 保留边界已由 D065 改为退役。 |
| D065 | superseded | D066、D067 | 旧 Node Lab 已全量退役，其本地历史证据随后按授权删除。 |
| D066 | updated | D067 | V1 可执行链路删除仍有效；历史本地产物保留策略由 D067 更新。 |
| D067 | accepted | — | 按用户明确授权删除整个本地 `output/` 及陈旧缓存/打包产物。 |
| D068 | accepted | — | Memory/checkpoint 实现与 PostgreSQL 数据休眠保留，不恢复旧 V1 接口。 |
| D069 | updated | D070 | ShaderGraph 已从非权威 shadow 切换为 F09 默认产品真相源。 |
| D070 | accepted | — | F09 保持 12 节点闭环，产品真相源使用有界 ShaderGraph。 |
| D071 | accepted | — | 本分支先优化 Author/感知边界并完成最小验证，参数优化转为跨分支 TODO。 |
| D072 | updated | D073 | acceptance live A/B 结论保留；“待切换生产 acceptance”已由 D073 纠正。 |
| D073 | updated | D070 | strict total-loss 语义继续有效；纯函数收口只覆盖 legacy MinScene Builder。 |
| D074 | updated | D070、D076 | 私有 replay 的安全边界保留在 legacy MinScene Builder；当前 ShaderGraph 产品尚未迁移该机制。 |
| D075 | superseded | D076 | 12/32 maturity 只适用于旧 Feature 候选空间，不授权当前产品预算变更。 |
| D076 | accepted | — | 停止旧 Feature 架构质量实验，并在 ShaderGraph 上重新冻结 benchmark。 |
| D077 | accepted | — | ShaderGraph compile 上限按 run 预算推导，预算耗尽收敛为候选失败。 |
| D078 | accepted | — | 无效 ShaderGraph Refine 走 no-op 过桥，不得重建参数队列。 |
| D079 | updated | D080 | kimi provider 注册保留；“复用 openai family”已由 D080 改为独立 kimi family。 |
| D080 | accepted | — | Kimi Code 端点仅允许 temperature=1，kimi 独立 family 固定温度，生产默认模型切换为 `kimi:k3-256k`。 |
| D081 | accepted | — | kimi family 经 `SHADER_GEN_KIMI_REASONING_EFFORT` 下发 `reasoning_effort`，默认 low。 |
| D083 | accepted | — | LayerPlan + 直接 GLSL Author 仅以非权威 shadow 实验接入，第一阶段只交付修订版设计基线；晋升新 ADR 方可取代 D070 执行表示部分。 |
| D084 | accepted | — | 实现隔离的 LayerPlan/direct GLSL shadow A/B harness；不接入生产 Graph、API 或 `current_best`。 |
| D085 | accepted | — | 前端运行可观测性只展示后端可证明的阶段事实，不伪造 active node、完整时长或最终 provenance。 |
| D086 | accepted | — | 冻结 LayerPlan shadow suite 的四样本、AB/BA 调度、预算指纹与预声明 gate；真实运行前 fail-closed 复验。 |
| D087 | accepted | — | 首轮真实 LayerPlan suite 自动 gate 失败，保持生产 no-go；先修复 direct GLSL 契约遵循稳定性再重新冻结实验。 |
| D088 | accepted | — | direct GLSL v2 只提高 shadow 两臂共用契约稳定性；生产路径保持不变，重跑前必须重新冻结实现身份。 |

## D001 - SVG 是最终架构来源

- 状态：`superseded`，已由 D050 取代；以下内容只保留历史审计价值。
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

## D034 - V1 确定性 Node 按流水线职责拆分

- 日期：2026-07-15
- 决策：删除 1605 行的 `png_to_shader_v1_run_nodes.py`，建立 `nodes/png_to_shader_v1/` 包，并以 `__init__.py` 作为 Graph 与 Node Lab 共用的稳定工厂入口。运行准备、候选物化、选择复核和最终收口各自独立；原本近 500 行的 `render_and_evaluate` 再拆成薄 Node 编排以及证据校验、真实 WebGL1 渲染、确定性评分三个内部阶段。旧导入路径不保留兼容层。
- 原因：原文件同时承载运行生命周期、Artifact、候选生成、Validator、Renderer、Evaluator、Selector 和资源清理，阅读或修改单一职责时必须理解整份文件，也容易让共享 helper 与 Graph Node 边界混淆。按流水线职责拆分后，每个文件只处理一种变化原因，同时仍保留一个生产 Node 的原子状态转换契约。
- 影响：20 个 Graph Node 名称、边、条件路由、停止路径和 `current_best` 语义完全不变，因此不产生新的 Graph 可视化节点。Node Lab descriptor 的 `source_ref` 改为实际职责模块，Provider 递归冻结嵌套 Node 源码；架构边界测试也递归扫描子包。新增包加入 `pyproject.toml`，Graph/Node Lab/测试统一改用新公开入口。

## D035 - 生成编排下沉 Backend Service，run 级 Renderer 使用双层清理

- 日期：2026-07-15
- 决策：`POST /api/shader/generate` 的项目锁、模式/模型选择、Legacy timeout、Agent 调用、生成总账、失败分类和 `ShaderResponse` 契约统一下沉到 `backend.app.services.shader_generation`；Route 只保留 HTTP 上传校验、应用依赖装配与稳定用例错误到 FastAPI envelope 的映射。PNG-to-Shader V1 的 Graph Builder 与 `PngToShaderV1Service` 共享同一个 run 级 Renderer registry：正常路径仍由 `finalize` 关闭，Service `invoke()` 的 `finally` 对越过 Graph 的未知异常执行限时、幂等兜底。
- 原因：原 Route 同时承担传输适配、并发控制、模型分流、持久化事务时序、错误分类和响应组装，难以单独阅读、复用和验证；Renderer 只依赖 `finalize` 时，任一未知 Node 异常或证据不变量破坏都可能绕过关闭路径并泄漏 Chromium 资源。两项调整都不需要改变 Graph 节点或公开 API。
- 影响：生成 Route 不再直接 import 生成过程总账函数或 Agent 生成代理，架构边界测试锁定该依赖方向；现有状态码、错误 envelope、日志字段、数据库时序和响应字段保持不变。Service 外层清理失败只记录 project/run 与安全异常类型，不打印底层原文，也不覆盖 Graph 结果或原异常。该修改没有新增、删除、重命名 Graph Node，也没有改变边、路由结果、循环、`current_best` 或终止路径；Graph ASCII/Mermaid 只补充 Graph 外资源边界说明。

## D036 - 产品与 LangGraph 入口收敛为 PNG-to-Shader V1

- 日期：2026-07-15
- 决策：`langgraph.json` 只注册 `png_to_shader_v1`；删除旧基础对话图和 `shader_generation_graph`，以及仅服务这两个旧图的 Node、State、Prompt、Parser 和 Agent Service。Backend/Frontend 同步下线 legacy 生成与独立 `POST /api/shader/review`，`POST /api/shader/generate` 只接受或默认使用 `procedural_v1`。
- 原因：用户确认其余两个 Graph 是此前遗留的废弃实现。只删除注册而继续保留 legacy API、默认 UI 和旧 Memory 生命周期会形成悬空入口，也会让“当前架构只有 V1”与运行事实不一致；因此必须沿反向依赖一起清理。
- 影响：旧客户端发送 `generation_mode=legacy` 或调用 `/api/shader/review` 不再兼容；V1 最终响应中的 Critic Review、评分、Artifact 白名单和项目 Memory 清理继续保留。已有数据库账本不删除，Memory 的旧 `review` 记录保留只读解析兼容；V1 checkpoint 前缀 `png-to-shader-v1:{project_id}` 保持不变。为维持项目删除语义，V1 清理同时删除旧 Graph 遗留的裸 `{project_id}` checkpoint，但不恢复任何旧运行入口。D009、D023、D027 以及 D035 中关于 legacy 分流和 timeout 兼容的部分由本决策取代；D035 确立的薄 Route、Backend 用例 Service 与 Renderer 双层清理边界继续保留。历史记录不删除。此次结构收敛不改变冻结质量门槛：F09 仍为 `active`、最终发布 gate 仍为 no-go。

## D037 - PROGRESS 采用有界当前交接与只读历史归档

- 日期：2026-07-15
- 决策：根目录 `PROGRESS.md` 不再作为 append-only 会话日志，而是原地维护的当前交接页，只保存当前状态、唯一 active 功能、下一步、未解决缺口、当前验证基线、最多 5 条最近重要变更和历史索引，UTF-8 体量上限为 20,000 bytes。只有功能状态、架构/契约、质量门禁、阶段里程碑或重要未决缺口变化时才新增最近变更；例行重复验证覆盖现有基线。超出内容移入 `docs/progress/archive/`，并明确标注时间范围和“非当前事实”。
- 原因：原文件在 8 天内增长到约 87 KB，94 条日期记录中的大部分是重复验证和已被后续实现取代的旧事实；它既消耗新 Agent 的有限上下文，又可能让旧 Graph 数量、已删除入口和过期环境判断干扰当前决策。功能状态、长期取舍、代码演进和冻结 benchmark 本来已经分别有 `docs/FEATURES.md`、`docs/DECISIONS.md`、Git 与产物目录作为更合适的事实来源。
- 影响：会话结束仍必须检查并更新当前交接信息，但“完成会话”本身不再触发新增日志。`make docs-check` 负责检查主文件必需区块、20,000 bytes 上限、最近重要变更不超过 5 条、历史索引和归档警告。失败 benchmark、人工门禁、run id、关键 SHA-256 与未关闭缺口不得在压缩中丢失；主文件保留当前结论和定位入口，结构化整理前的原始记录无损保存在 `docs/progress/archive/PROGRESS-2026-07-07--2026-07-15.md`。

## D038 - PNG-to-Shader V1 Node 统一使用功能命名空间

- 日期：2026-07-15
- 决策：`src/agent/app/nodes/png_to_shader_v1/` 作为当前产品 Pipeline 全部 Node 工厂及其支持实现的唯一功能命名空间，内部明确分为 `model/`、`deterministic/` 和 `integrations/node_lab/`。Graph 通过顶层 `__init__.py` 使用稳定工厂；模型角色保持不依赖 Renderer/Oracle/Store，确定性事实层保持不依赖模型角色，Node Lab Provider 只向内适配这两类生产 callable。删除 `nodes/` 根目录的 V1 兼容模块和原 `nodes/integrations/` 路径，不保留旧 import shim；只有被两个以上 Pipeline 实际复用且契约中立的实现，才允许提升到根级公共模块。与条件边共享规则的 `decide_after_render`、`decide_after_selection` 继续由 `graphs/png_to_shader_v1_routing.py` 管理。
- 原因：此前目录同时使用“模型角色/确定性实现”和“V1/公共实现”两套分类轴。`png_to_shader_v1/` 只包含确定性 Node，但根目录的 bounded、structured output、Analysis、Author、Critic、Context 和策略晋升同样绑定 V1 契约、Prompt、State 或阶段预算，造成根级文件看似跨版本通用、实际无法独立复用。统一功能命名空间能让版本归属、依赖方向和未来 V2/第二 Pipeline 的隔离边界从目录结构直接可见，同时保留 D034 拆分大型确定性实现的收益。
- 影响：内部 Python import、setuptools 显式 package 清单、Node Lab descriptor `source_ref`、执行 provenance 的真实 `source_ref#node_id` 和 benchmark 源码 fingerprint 路径同步更新；旧模块路径不再兼容。20 个 Graph Node ID、直接边、条件边、路由结果、循环、终止路径、`current_best` 安全语义、Node Lab 对外 node id、产品 API、checkpoint/Memory 和冻结质量门槛均不改变，因此 Graph ASCII/Mermaid 无需改图。D034 的职责拆分继续有效，其旧目录描述由本决策更新；历史决策正文不回写。

## D039 - Validator 与 Renderer 只接受 canonical V1 契约

- 日期：2026-07-16
- 决策：当前 `validate_shader()` 和 `PlaywrightWebGL1Renderer` 只接受与 `WEBGL1_STATIC_NO_TEXTURE_V1` 值相等的 canonical 契约；传入仅修改 contract id 或任一运行字段的 `RenderContract` 必须在启动浏览器前失败，不得回显新 contract id 后继续使用 V1 的硬编码声明、顶点 Shader 和安全规则。
- 原因：现有 Validator 与 WebGL host 是明确的 V1 专用实现，并不能正确解释任意 `RenderContract`。假装支持自定义契约会让审计身份与实际校验规则不一致，比显式限制支持范围更危险。
- 影响：值相等的不可变契约副本仍可使用；未来支持新契约时必须先实现对应 Validator/Renderer 语义和 conformance 测试，再放宽入口。Validation 文档同时明确确定性 smoothstep 修复不是隐式校验步骤，修复后必须重新校验和渲染。

## D040 - Benchmark 异常路径按 fail-closed 方式扣除模型预算

- 日期：2026-07-16
- 决策：AI-on runner 在模型调用返回后立即保留 State；后续 Artifact、objective、traceability 或报告处理抛错时，优先从模型调用审计和 final result 恢复实际调用数。无法取得可靠计数时，按该 case 在调用前获得的模型调用上限保守扣账。异常与取消都先写安全失败证据和 case 结果再停止或继续，恢复旧式无证据失败结果时同样不得信任零调用。
- 原因：付费调用可能在后处理失败前已经发生。把异常 case 固定记为零会让后续 case 重新获得已经消费的预算，破坏整套硬预算和成本审计；保守高估比静默超支安全。
- 影响：失败证据只记录异常类型、阶段、预算记账策略、安全模型审计和候选 Artifact 引用，不保存异常消息、Prompt、GLSL 或 reasoning 原文。`result.json` 是 case 完成的原子提交点；其他 Artifact 可能先落盘，不能被描述为整组文件原子事务。

## D041 - 验收证据显式区分 durable 与 partial

- 日期：2026-07-16
- 决策：新增 `docs/evidence/registry.json` 作为版本化脱敏索引，登记正式 run 的结论、Artifact 路径或 URI、字节数、SHA-256 和耐久性。只有已进入 Git、Git LFS、Release 或具备不可变保留策略对象存储的关键证据可以标记 `durable`；仅存在于 `.gitignore` 下本地 `output/` 的完整报告必须标记 `partial`，即使 registry 已记录其 hash。
- 原因：hash 只能验证已经取得的文件，不能保证新 clone、CI 或机器损坏后仍能获得原件。把本地路径写进 PROGRESS 并不等于建立可复验发布证据。
- 影响：`make docs-check` 在本地 Artifact 可获得时复验 registry 的大小与 SHA-256，并强制所有持久 Artifact 存在；`partial` 可以用于交接和定位，但不能单独支撑功能 `passing` 或发布 gate。完整 M5 和 Node Lab real-model 报告迁入持久介质前，该缺口继续保留在 `PROGRESS.md`。

## D042 - 主 CI 复用仓库门禁并严格消费锁文件

- 日期：2026-07-16
- 决策：Pull Request 与 main 的主 CI 在 Python 3.12、Node 22 下执行 `uv sync --locked`、`npm ci --prefix frontend`、完整 `make check` 以及 `mypy --strict src backend`，并以 `UV_LOCKED=1` 禁止后续 `uv run` 隐式改写锁文件；Python 3.10/3.11 只追加兼容性单测。普通 Integration workflow 同样严格使用 `uv.lock`，显式关闭真实模型路径且不注入模型密钥；只有独立 benchmark workflow 可以在双重开关和硬预算下获得真实模型凭据。
- 原因：原 CI 从 `pyproject.toml` 临时解析依赖并重复安装测试工具，既不能证明 `uv.lock` 可复现，也没有执行文档边界、LangGraph 注册和前端 production build。普通集成测试持有模型密钥还会模糊“真实模型只由显式 benchmark 调用”的安全边界。
- 影响：`make docs-check` 会拒绝未锁定的 workflow、缺失 `make check`/最低 Python 兼容矩阵或普通 Integration 中出现模型凭据与调用开关。Integration workflow 同时覆盖 main push 与相关路径的 Pull Request，主 CI 构建 wheel 以防源码树通过但发布包缺文件。Renderer benchmark 与集成测试仍单独安装 Chromium；有限保留期的 Actions Artifact 继续只算 `partial` 证据。

## D043 - 包根惰性导出，Backend SQL 使用显式资源包

- 日期：2026-07-16
- 决策：`shaderforge`、`agent.app.lab` 与 `agent.app.contracts` 根包通过 PEP 562 按所属 typed 子包惰性解析既有公共名，保持兼容 API，但轻量契约、LLM 抽象或 Lab 模型导入不得连带加载 V1 契约、Renderer、Runner 或 Playwright。`backend/sql/` 增加无副作用 `__init__.py` 并在 setuptools 中显式登记为 `backend.sql`，SQL package-data 由该包直接持有；项目许可证元数据改用当前 PEP 621 字符串和显式 license file，三个发布包根均携带 `py.typed`。
- 原因：Python 导入子模块会先执行父包 `__init__`；此前仅导入 `shaderforge.contracts` 或 `agent.app.lab.models` 就加载完整聚合入口和浏览器依赖。与此同时，SQL 目录既被当作 `backend` 数据目录又会被构建工具识别为隐式 namespace package，源码树与 wheel 的所有权不明确。
- 影响：显式使用 `shaderforge.public` 仍会加载完整稳定聚合面；访问根包某个惰性名只加载其所属领域。回归探针验证轻量导入集合、根包对象 identity、wheel 内 `backend.sql`、SQL 资源与类型标记可读性；wheel 构建不得产生 package-discovery 警告。新增包仍需同步 setuptools 清单、架构文档和 wheel 审计。

## D044 - Backend 在组合根冻结配置并对资源执行补偿清理

- 日期：2026-07-16
- 决策：Backend 使用不可变 `BackendSettings` 在应用组合根一次性读取数据库、日志、CORS 与 Node Lab 环境配置，再显式注入 Router、Service 和 lifespan。数据库及 Agent Memory open 函数负责在初始化失败或取消时回收自身局部资源；成功资源交由 `AsyncExitStack` 逆序关闭，即使一个 close 失败也继续执行其余 callback。close 在等待底层 pool 前先从 `app.state` 脱离对象，运行期 service 也在统一清理 callback 中失效。
- 原因：数据库 pool 曾在统一 `try/finally` 外打开，Memory close 抛错会跳过 asyncpg close，初始化中断也可能留下半打开资源。各模块重复读取环境变量还会让 import 时 Router、生命周期和惰性 Node Lab Service 看到不同配置。
- 影响：`create_app(settings)` 与 `build_lifespan(settings)` 可被无环境副作用地测试；Node Lab 继续默认关闭，真实模型仍需独立显式开关。`SHADERGEN_CORS_ORIGINS` 采用逗号分隔的显式 Origin 且拒绝 `*`。该决定不改变产品 HTTP 路径、Graph、模型预算或 F09 发布状态。

## D045 - 离线 benchmark 与测试 Fixture 脱离在线 Service 和测试层级

- 日期：2026-07-16
- 决策：五模型角色离线 benchmark 从 `agent.app.services` 移入 `agent.app.benchmarks`，CLI 与测试通过该离线入口调用；生产 `services` 只保留在线 Application use case。跨单元/集成测试共享的 PNG-to-Shader 样本从 `tests/unit_tests` 移入中立的 `tests/fixtures`，集成测试不得导入 unit test 模块。
- 原因：离线 runner、固定 manifest、预算和报告协议不是线上用例服务，把它们放入 `services` 会扩大生产依赖面并模糊真实模型调用边界。集成测试反向复用 `unit_tests` 也会把测试执行层级当作公共库，目录重排时容易产生隐蔽耦合。
- 影响：benchmark 的模型角色、fixture、预算、报告与真实模型双重开关保持不变；普通线上 Agent service 不依赖离线 runner。仓库结构测试锁定 `services -> benchmarks` 和 `integration_tests -> unit_tests` 两条禁止依赖，新增共享样本应继续放入 `tests/fixtures`。

## D046 - 前端通过统一 API Client 访问后端，Node Lab 明细按需加载

- 日期：2026-07-16
- 决策：所有浏览器端 HTTP 访问统一经过 `frontend/src/api/client.ts`，由它负责 API base URL、请求发送和安全错误提取；页面与组件不得直接 `fetch`。Node Lab 的步骤列表直接使用后端已有 summary，完整步骤只在选中时按需读取并缓存，Artifact descriptor 使用独立状态维护。
- 原因：组件自行拼 URL 会让环境配置、错误语义和 URL 编码分散；原 Node Lab 恢复 DAG 时先列 id、再逐项拉取完整步骤，会随历史长度产生 N+1 请求，并把步骤与 Artifact 两类资源混入同一刷新路径。
- 影响：产品与 Node Lab 的公开 HTTP 路径和 Schema 不变；假 API 也必须经 Backend response model 校验 summary 契约。仓库结构测试拒绝 `frontend/src/api` 之外的直接 `fetch`，Node Lab 页面验收继续覆盖恢复、分支、Artifact 与 DAG 行为。

## D047 - V2–V5 按版本契约递进实施，先冻结 V2.0 再扩展 Pipeline

- 日期：2026-07-16
- 决策：后续 PNG-to-Shader 演进采用 `human_doc/png-to-shader-v2-v5-plan/` 中已正式 Review 的总纲与四版本方案：V2 建立 TargetHypothesis、RequestConstraintSet、Intent IR、Effect Genome 和 Deterministic Compiler；V3 在固定拓扑上建立版本化 Oracle、SelectionKey、SearchJournal 和确定性参数搜索；V4 只在结构停滞后通过受限 GenomePatch、版本化 shortlist、Pairwise/HITL 和 staging SelectionSnapshot 改变结构或偏好；V5 再引入 Async Run、Ledger、Checkpoint/NodeCommit、RunJob/RendererJob 双 fencing、SSE、取消和恢复。Review 结论为 Conditional Go，仅允许先实施 V2.0 Schema、Hash、Artifact Adapter、golden fixture、数据 Manifest 和 State 恢复契约；不得跳过 V2.0 并行启动 Prompt、Compiler、Search 或新 Graph。
- 原因：第一稿在多测量假设、ConstraintSet 集合级身份、跨版本 State、Search 恢复、evaluation revision 原子发布、Renderer 幂等与 durable 副作用等位置存在可编码性断层；把 V2–V5 合成一个大任务会迫使后续版本依赖仍在变化的根契约。正式 Review 已将事实、推断、约束、Genome、Evidence、选择语义和持久化边界拆成可独立冻结的版本层，并为质量、人工评测和恢复建立可重复判定协议。
- 影响：当前 F09 仍是唯一 `active` 功能，F02–F05 及异步产品能力继续为 `not_started`；本决策和方案文档不改变现有 V1 Graph、API、RenderContract、`current_best`、checkpoint 或发布 gate。F09 M6.2 证据冻结后，首个实现 PR 只交付 V2.0 契约和测试资产；后续每个增量必须满足总纲的一次一个 active 功能、版本化 Manifest、V1 只读兼容和对应退出门槛。

## D048 - 最小 scene/template/optimization Graph 先并行验证，不提前替换 V1 产品链路

- 日期：2026-07-21
- 决策：在 F09 内新增 `png_to_shader_min` 技术验证路线，以严格 scene JSON 作为唯一可编辑 Shader 表示，由模板生成参数化 WebGL1 GLSL，LLM 只生成初始 scene 和单个 typed patch，数值拟合交给节点内 CMA-ES。该路线先完成 prepared Renderer/typed uniform 与轻量 MAE 性能门禁，再按 12 节点、3 个纯路由函数实现 Graph/CLI。最小图与现有 `png_to_shader_v1` 并行注册；在独立产品切换里程碑完成前，V1 继续作为唯一 Backend/Frontend 产品路径。
- 原因：V1 的自由 GLSL 路线已通过自动事实门禁但人工偏好率只有 30%，结构与数值耦合使确定性优化难以介入；scene/template 路线可以把模型结构决策与数值搜索分离。但当前 Renderer 每次重新编译并编码 PNG，不支持任意 uniform 热路径，且快速版只定义 CLI 验收，尚不足以安全替换 API、UI、Memory、Node Lab 和 benchmark。
- 影响：D047 的 V2–V5 仍是目标架构输入，但在进入其大范围契约实施前先形成这条有界技术证据；快速版不改变 F09 的 `active` 状态，不把 F02–F05 标为已开始。现有 V1 代码、发布 gate、历史 benchmark 和失败证据不得因最小图开发而删除或覆盖。只有 M7 通过 Backend integration、浏览器 E2E、生命周期、Artifact 和文档门禁后，才能另行决定下线 V1。

## D049 - 目标架构详细版取代 V2–V5 作为 F09 向前实施依据

- 日期：2026-07-21
- 决策：`human_doc/PNG转无贴图Shader-Agent-目标架构详细版.md` 成为 F09 后续算法与演进的权威目标，`human_doc/PNG转无贴图Shader-Agent-最小骨架快速版.md` 是当前 MVP 实施切片。旧 `human_doc/png-to-shader-v2-v5-plan/` 及 D047 继续保留为历史审计和概念参考，但不再规定向前实现顺序、Schema、Graph 或阶段冻结门禁。本决策取代 D048 中“D047 的 V2–V5 仍是目标架构输入”的表述；D048 关于最小图并行验证、V1 产品链路保留和 M7 切换门禁的其余部分继续有效。
- 原因：用户已明确选择新的 scene/template/optimization 详细架构取代旧 V2–V5 路线。继续同时维护两套权威阶段会让 MVP 在 State、场景表示、优化调度和升级顺序上出现互斥约束，无法形成单一可执行路线。
- 影响：F09 算法目标改由详细版约束。当前只有 F09 为 `active`，F02–F05 状态不变；详细版中的多假设、置信度标定、特征块调度、沙箱和完整评测仍是未实现目标。旧 V1 代码、API、UI、Node Lab、benchmark 和冻结失败证据在独立 M7 通过前继续保留，不因路线切换而删除或覆盖。总体架构材料的权威关系由 D050 进一步澄清。

## D050 - 初始架构 SVG 降为历史设计参考

- 日期：2026-07-21
- 决策：`human_doc/shaderforge-technical-architecture-aligned(1).svg` 只作为项目最初产品设计和历史背景参考，不再是当前或最终架构的覆盖性权威。本决策取代 D001。当前实现事实由代码、运行配置、`docs/ARCHITECTURE.md` 和模块旁架构文档共同记录；F09 的目标与当前实施切片分别以目标架构详细版和最小骨架快速版为准。
- 原因：SVG 反映的是项目早期设想，后续已经形成经过验证的 V1 实现和用户明确确认的新 scene/template/optimization 方案。继续规定“冲突时以 SVG 为准”会让历史草图反向覆盖新决策和可验证事实。
- 影响：README、AGENTS 和全局架构文档不再使用“最终架构以 SVG 为准”的表述。SVG 文件继续原样保留，不删除、不静默改写；可以用于追溯最初产品概念，但不能据此否决后续方案、决策、契约或代码。

## D051 - 先用确定性 scene_mvp 贯通产品垂直切片

- 日期：2026-07-21
- 决策：在保留 `procedural_v1` 默认路径的同时，增加显式 `scene_mvp` 模式并同步接入 Graph、Agent Service、Backend、Artifact API 和 Frontend。首个可运行增量保留快速版的 12 节点/3 路由结构，但用确定性感知 fallback 完成 Initial Author，模型预算固定为 0；typed uniform 先确定性烘焙为常量并复用现有 Renderer，基础优化仅做少量有界微调，不引入 CMA 依赖。
- 原因：本阶段目标是尽快验证 scene → 模板 → 真实 Renderer → MAE → Artifact → HTTP → UI 的职责和追踪链路。prepared program、模型结构输出和 CMA-ES 同时落地会扩大故障面，也不符合当前“避免过度设计和过度优化”的实施要求。
- 影响：`scene_mvp` 必须明确标记为实验路径，不能把当前轻量微调表述为 CMA-ES 或性能门禁已经完成；V1、Memory、Node Lab、既有 benchmark 和冻结失败证据全部保留。后续优先补 prepared program，再决定是否引入模型 Author 与 CMA-ES；F09 继续 `active`，发布 gate 仍为 no-go。

## D052 - scene_mvp 使用同 run prepared uniform 热路径

- 日期：2026-07-21
- 决策：`scene_mvp` 在同一 run 内按模板源码、尺寸和 typed uniform schema 形成唯一 prepared program 签名，只静态校验、编译和链接一次；每个候选必须完整上传白名单内的 `float`、`vec2`、`vec3` 值集并直接读取左上角行序 RGB。未接受候选不编码 PNG；首个有效候选和最终接受候选保留 PNG，最终 WebGL1 GLSL 继续把 uniform 烘焙为常量，以兼容旧 `render()` 和独立预览。prepared 对象只由 run registry 持有，不进入 LangGraph State。
- 原因：后续数值优化需要高频 draw，原有每候选重新编译、链接和 PNG base64 编码会把 Renderer 开销混入搜索预算。固定模板配合严格 uniform 全量上传既能缩短热路径，也能避免缺失值沿用上一帧；保留自包含最终 GLSL 则不把运行时 prepared 生命周期泄漏到 Artifact 和前端。
- 影响：公开 `scene_mvp` 摘要、账本、metrics、manifest 和 finalize trace 固定增加 `renderer_path=prepared_uniforms_v1`、目标 MAE、是否达标、prepare 耗时、uniform draw 数及 P95。192x192 粉球 100 draw 探针必须显式运行并满足总耗时不超过 45 秒、P95 不超过 450 ms 和无陈旧帧；通过该先决门禁不等于 CMA-ES、2000 draw 生产预算或质量发布门禁已经完成。D051 的确定性 fallback、V1 默认路径和 F09 no-go 继续有效。

## D053 - scene_mvp Model Author 只产出完整 scene 或单个 typed patch

- 日期：2026-07-21
- 决策：保持 `png_to_shader_min` 的 12 节点、直接边、条件边和路由结果不变，由 Graph Builder 复用 `LLMGateway`/`LangChainLLMGateway` 注入 Author。Initial 只有在 `llm_budget>0` 时调用模型并严格解析与参考图画布绑定的完整 MinScene；调用、结构修复或解析失败回退到确定性感知 scene。Refine 只接受恰好一个 Pydantic 联合类型 patch，白名单固定为 `/object/features` 的 add/remove 和 `/object/color_field/model` 的 replace，候选必须从 `current_best.scene` 派生，不能直接更新 best。
- 原因：scene/template 路线需要先验证模型结构变更的最小安全面；允许任意 JSON Patch、多个操作或从工作 scene 连续派生会扩大模板不变量、prepared program 和 best 单调性的风险。完整 Initial 加单个 typed Refine 可把模型职责限制在结构选择，真实 Renderer/MAE 继续拥有接受权。
- 影响：语义调用和最多一次同模型结构修复共用 run 级 6 次硬上限；显式 `scene_mvp` 产品模式使用该上限并限制 1 轮 Refine，未配置密钥或供应商失败时回退确定性感知 scene。普通测试只注入 Fake Gateway。Refine 的工作候选必须经真实渲染且 MAE 严格改善才能覆盖 `current_best`；非法 patch、供应商异常、解析失败和较差候选全部保留原 best。此增量不改变 F09 active/no-go、V1 默认产品路径或 M7 切换门禁。

## D054 - scene_mvp 先接入小预算确定性参数搜索

- 日期：2026-07-21
- 决策：`optimize_base` 和 `optimize_feature` 通过 `shaderforge.optimization` 使用固定顺序、单参数、严格白名单的数值邻域候选。base 覆盖主体 center/axes、背景与径向渐变参数；feature 覆盖现有 rim/shadow 等特征的 center/axes/color/intensity。候选按剩余 draw 预算截断，单批硬上限 24，产品 `scene_mvp` 整 run 暂用 40 draw；所有候选串行真实渲染，只有 MAE 严格下降才更新 `current_best`。
- 原因：prepared 性能门禁已经通过，但直接进入 2000 draw CMA-ES 会同时放大算法、预算和请求时延风险。小批确定性搜索足以先验证参数接线、预算记账、回滚与可观测 trace，并保持实现可读和故障范围有限。
- 影响：`scene_mvp` 不再只有轴长两个占位微调，基础和 feature 节点都会产生真实 uniform draw；accepted parameter 与候选数写入 trace。该实现不是 CMA-ES、没有随机/并行搜索，也不构成质量发布证据；后续扩大到 2000 draw 必须作为独立增量重新验证时延、取消和 benchmark。

## D055 - scene_mvp 以真实仲裁、多特征和局部复合 loss 加固最小闭环

- 日期：2026-07-22
- 决策：保持 12 节点与路由拓扑不变，但 Initial 模型 scene 与确定性感知 fallback 在预算允许时分别真实渲染并按 `min_scene_composite_v2` 择优；固定模板保留 8 个 feature slot，逐项消费 type/center/axes/color/intensity，优化队列来自获胜 scene 的稳定 feature id。参数 proposal 必须重放到最新 best，使同批接受结果累计生效。质量档位取代固定 40 draw/1 Refine：`fast|balanced|high` 的 render/LLM/Refine 硬预算分别为 `48/2/1`、`96/4/2`、`160/6/3`。`current_best` 选择与达标判断使用整图、前景、高光、阴影 MAE 按 `0.35/0.35/0.15/0.15` 组合的复合 loss，整图 MAE 只保留为兼容诊断。
- 原因：真实 run 已证明模型初稿可能比确定性 fallback 更差；旧模板只消费首个 rim/shadow 且忽略部分 feature 参数，固定 feature queue 与从旧 baseline 生成整批候选也会造成优化无效或已接受变化丢失。单一整图 MAE 还会被大面积背景稀释，无法可靠表达主体、高光和阴影质量。
- 影响：本决策取代 D053 的固定 1 轮 Refine 产品限制和 D054 的 24/40 固定搜索预算，但保留 6 次整 run 模型硬上限、typed patch、真实 Renderer 接受权与无 CMA-ES 边界。API、账本、metrics、manifest、trace 和前端摘要同步公开质量档位、预算用量、复合 loss 与局部指标。该改动提升最小骨架的可解释优化能力，但未运行新的真实模型质量 benchmark，F09 继续 `active`、灰度 no-go。

## D056 - packed 三槽模板以 WebGL1 最低 uniform 容量为硬边界

- 日期：2026-07-22
- 决策：将 D055 的 8 个独立 uniform feature slot 收紧为 3 个 packed slot，并把不兼容 Scene/模板分别升级为 `png_to_shader_min_scene_v2` 和 `png_to_shader_min_template_v2`。Scene 基础参数固定使用 4 个 `vec4`，每个 feature 使用 meta/shape/color 3 个 `vec4`；加上 Renderer 管理且静态使用的 `u_resolution` 后，最坏为 14 个 active fragment uniform vectors，低于 WebGL1 最低保证的 16，物化时再次 fail-closed 校验。prepared Renderer 的 typed uniform 白名单扩展到 `vec4`。`rim`、`polar_arc`、`edge_line` 分别使用主体边界带、上半椭圆弧和有限长度线带，禁止不同 schema 类型退化为同一公式。
- 原因：原 8 槽布局超过 WebGL1 最低 fragment uniform 容量，桌面 Chromium 通过不能证明约束设备可链接；同时旧 body feature 分支把三个类型合并为同一权重，模型声明的弧和线没有像素语义。沿用 v1 版本还会让不兼容 uniform/像素公式共享 provenance。
- 影响：本决策取代 D055 的 8-slot 部分和 D052 中 prepared 白名单仅含 float/vec2/vec3 的限制；三槽上限进入 `MinScene` 严格 Schema，超过上限的模型输出安全回退。metrics、manifest、账本和 API 摘要显式记录 v2 模板版本。真实 Chromium 集成测试必须同时证明 legacy/prepared 像素一致和三种 body feature 像素互异；F09 状态与发布 no-go 不变。

## D057 - scene_mvp 运行进度用进程内存事件缓冲加增量轮询

- 日期：2026-07-22
- 决策：scene_mvp 的运行时可观测采用“内存事件缓冲 + 前端增量轮询”，不引入 SSE/WebSocket。前端预生成 UUID `run_id` 随 POST 显式发送（服务端缺省自生成的行为不变，进行中 run_id 冲突返回 409 `run_conflict`），随后以约 1.2s 间隔轮询 `GET /api/shader/runs/{run_id}/progress?after=<seq>`。Agent service 把 `ainvoke` 换成 `astream(stream_mode="updates")`，逐节点产出严格白名单事件（trace 差分、counters、best、decide 路由），渲染帧字节经独立通道保存最近一帧；两者写入 `backend.app.services.run_progress.RunProgressRegistry`（单进程单 worker、重启即失、惰性 TTL 清扫）。
- 原因：仓库此前没有任何流式基础设施，`agent_runs` schema 注释本就预留“前端轮询”语义；scene_mvp 单次运行 1–12 分钟，轮询足够且 TestClient 易测，SSE 的连接生命周期、重连和测试成本在现阶段不成比例。运行中途不写 `agent_events`：终态单事务账本语义保持不变，内存缓冲只服务运行中的页面观测。事件之所以必须白名单化，是因为 state update 直接携带图片、Scene、GLSL 与渲染字节。
- 影响：`POST /api/shader/generate` 新增可选 `run_id` 表单字段；新增 `/progress` 与 `/progress/render` 两个只读端点，未知 run_id 返回 `pending` 以吸收客户端先于服务端登记的竞态。Graph 拓扑、路由和终态账本不变；`procedural_v1` 不发布进度。多 worker 部署下进度不可见（事件只存在于执行该 run 的进程），属于已接受的单 worker 限制，后续需要时再换共享事件总线。

## D058 - scene_mvp 先做固定模板扩展，不引入动态 Compiler

- 日期：2026-07-22
- 决策：F09 的下一质量增量采用 `docs/superpowers/specs/2026-07-22-scene-mvp-fixed-template-expansion-design.md`。继续限定单主体 `circle|ellipse`，颜色场支持具有真实像素语义的 `solid|radial|linear`，feature 保留既有四类并新增主体内 `gaussian_lobe` 与主体外 `glow`；每个 feature 压成 2 个 `vec4`，四个槽连同基础 Scene、类型元数据和 `u_resolution` 最坏使用 15 个 fragment uniform vectors。Refine 新增按稳定 id 的原子 `replace_feature` 和完整 `replace_color_field`。Graph 拓扑、同 run 单 prepared program、默认 `procedural_v1` 和显式 `scene_mvp` 产品边界不变。该方案名不是项目阶段号，与已废弃为历史参考的旧 V3 Oracle/Search 方案无关；Scene/template/metric 的正式版本号在实现增量冻结。
- 原因：粉色凝胶球实测证明当前单一 radial 场、三槽和亮度分位数 objective 存在明显表达与评价缺口，但一次引入动态结构编译、最多 8 个逻辑 feature、多几何、自动残差分类和自动 procedural fallback 会同时改写 Renderer 生命周期、资源规划、感知、Graph/Backend 编排和预算语义。固定模板扩展可以在 WebGL1 最低容量内增加通用颜色场与局部效果，同时保持 Initial/fallback/Refine 共用唯一 program 签名和现有 current_best 安全边界。
- 影响：实现已分别冻结 `png_to_shader_min_scene_v3`、`png_to_shader_min_template_v3`、`min_scene_composite_v3`；固定 7 例 deterministic fallback 的内部 loss 中位数约 `0.0402`，据此冻结 `target_loss=0.04`。三类颜色场、circle/ellipse、六类 feature、四槽 15/16 资源边界和 replace patch 均有聚焦测试。相同 7 例用外部 `png_to_shader_score_v1` 对照 v2 fallback，v3 为 6/7 改善且其余 global/ROI/bbox 回归未越过预设容差。本轮仍不支持 `rounded_rect`、`ring`、`dual_disks`，不自动切换 `procedural_v1`，不引入 CMA-ES、动态 ROI、逐 feature 消融或多 program cache。该工程证据不等于真实模型或人工偏好门禁；F09 继续 `active/no-go`。

## D059 - scene_mvp 目标与分档预算统一由 YAML 启动配置

- 日期：2026-07-22
- 决策：把 `scene_mvp` 的 `target_mae`、`target_loss` 以及 fast/balanced/high 三档 render/LLM/Refine 硬预算迁移到包资源 `src/agent/app/config/png_to_shader_min.yaml`。Agent 在进程导入时一次性加载，要求三个公开档位完整存在，并严格拒绝未知字段、错误类型、负预算以及超出 `[0,1]` 的目标；Model Author 的 run 级调用上限从三档最大 `llm_budget` 推导，不再保留独立数值常量。修改配置后必须重启进程。
- 原因：目标和预算原本同时散落在 Service、Model Author 与节点缺省值中；仅修改一处可能被另一处的 6 次上限或 `0.08/0.04` fallback 截断，造成 UI 显示、实际停止和模型用量不一致。单一严格 YAML 能让 Backend 进度、Artifact、账本和前端继续记录实际注入值，同时保持配置入口可读。
- 影响：默认行为仍为 MAE/loss `0.08/0.04`，三档 render/LLM/Refine 仍为 `48/2/1`、`96/4/2`、`160/6/3`；公开 API 和 Graph 拓扑不变。当前 `target_loss` 继续是停止与 `target_reached` 的唯一质量条件，`target_mae` 只用于诊断展示。D058 的 `0.04` 是现有 benchmark 证据对应的冻结默认值；运维可以改 YAML 做实验，但变更目标或预算后的 run 不得冒充原冻结配置的可比证据，正式 gate 必须记录实际值并重新验收。

## D060 - scene_mvp Graph 安全上限由合法预算路径推导

- 日期：2026-07-23
- 决策：废止 `scene_mvp` 固定 `recursion_limit=64`。配置加载时按 `R=min(refine_budget,max(llm_budget-1,0))`、固定模板最多四个 feature 和当前 12 节点路由推导最坏节点步数 `9 + 2F + R × (6 + 2F)`，每个 run 注入该值加 4 步框架余量；推导结果超过全局防御上限 256 时拒绝启动。`GraphRecursionError` 继续作为 `internal_pipeline_error` fail-closed，不允许异常时静默导出 `current_best`。失败账本额外保存内存进度中的 `latest_seq/current_node/counters/best/budgets` 安全快照，不保存事件、图片、输入、Scene 或 GLSL。
- 原因：run `9d10b919-25f6-41a2-a2cf-e88c23ad78be` 在 high 档实验预算 `640/9/9` 下于合法的 `decide_after_feature -> optimize_feature` 路径第 64 步被框架中断，当时 render/LLM/Refine 为 `333/6/5`，不是路由死循环。原 high 档 `160/6/3` 的四 feature 最坏路径约 59 步，固定 64 只是偶然覆盖旧预算；预算迁移到 YAML 后没有同步其二级安全边界。
- 影响：当前 high 档最多八轮 Refine 的合法路径需要 129 步，run 级上限为 133；四 feature 最大预算集成测试必须真实执行超过 64 步并由业务预算正常结束。当前 YAML 的 `0.04/0.02` 目标与 `48/2/1`、`96/4/2`、`640/9/9` 预算是区别于 D058/D059 冻结基线的实验配置，相关 run 不得与旧七例 baseline 混算。Graph 节点、边、路由、终止路径和 `current_best` 安全语义均未改变。

## D061 - scene_mvp Refine 使用可审计的有界候选成熟

- 日期：2026-07-23
- 决策：保持 `png_to_shader_min` 的 12 节点、直接边、条件边和路由结果不变，但改变 Refine 候选选择语义。每个合法且未与最近拒绝记录重复的 typed Patch 从只读 `current_best` 派生独立 branch，先执行 1 次 raw draw，再按 Patch 影响范围执行最多 11 次确定性局部 draw；add/replace feature 只调整该稳定 feature，replace color field 只调整颜色场 bindings，remove 只做 raw 重评分。只有 matured candidate 的 `min_scene_composite_v3` loss 严格更低才原子提交；非法、重复、Renderer 失败或成熟后仍较差的 branch 整体丢弃。Refine 后 `optimize_base` 仅作 no-op 过桥，不再重新执行完整 base/feature sweep。单 Patch 12 draw 全部计入现有 run 硬预算，不增加隐藏预算。
- 原因：真实 run `85506ab8-12c4-4a20-8940-824875ea0f97` 中 Initial 像素结果与 fallback 等价，五次 Refine raw 候选全部在进入 feature optimizer 前被拒绝，而 320 次 draw 主要消耗在重复全量 sweep。首帧 Patch 同时猜中位置、尺寸、颜色与强度才可挑战已成熟 best 的规则，会系统性淘汰结构方向正确但初始参数不成熟的候选。终态又缺少 Patch operation/type、指纹、分量 delta 和拒绝历史，无法区分结构错误、参数未成熟或重复提案。
- 影响：空间残差新增固定 4×4 top-2 tile 的 MAE 与 `rendered-reference` signed luminance/RGB bias，但不改变 scorer、权重或 metric version。Refine Prompt 同时获得主导 metric、active feature 和最近三个拒绝摘要。Trace、metrics/manifest、API 与终态账本只保存 operation、feature id/type、规范 SHA-256、raw/matured metric delta、拒绝原因、重复标记和耗时，禁止保存完整 Patch、图片、GLSL、用户输入、模型原始响应或 reasoning。YAML 必须显式声明 `frozen_benchmark|independent_experiment`、独立实验 ID 和报告版本，并生成配置指纹；冻结身份若偏离 D058/D059 的 `0.08/0.04` 与 `48/2/1`、`96/4/2`、`160/6/3` 则启动失败。当前 `0.04/0.02 + 48/2/1、96/4/2、640/9/9` 继续只属于独立实验。Refine 不再反复遍历 feature 后，D060 的路径公式由 `9+2F+R×(6+2F)` 修正为 `9+2F+6R`，当前 high 为 65 步、注入上限 69；D060 的 fail-closed 与全局 256 防御边界保留。该工程实现只证明机制与安全性，未证明真实模型质量收益；geometry 语义修正、固定 7 例真实模型 benchmark 和匿名人工偏好仍需独立执行，F09 保持 `active/no-go`。

## D062 - scene_mvp 新增仅限独立实验的 manual 预算档

- 日期：2026-07-23
- 决策：保留 fast/balanced/high=`48/2/1`、`96/4/2`、`640/9/9` 的当前可比语义，新增 `scene_mvp` 专用 manual=`1000/32/30` render/LLM/Refine 档。独立实验 YAML 必须完整声明四档；D058/D059 冻结 benchmark 仍只允许原三档 `48/2/1`、`96/4/2`、`160/6/3`，携带 manual 必须 fail closed。Backend 表单契约接受 manual，但 `procedural_v1 + manual` 返回 422；Frontend 仅在 `scene_mvp` 下展示 Manual，从 Manual 切回 V1 时回落到 V1 high。
- 原因：把 `1000/32/30` 直接覆盖 high 会破坏既有 high run、测试和报告的配置可比性，也会把显著更高的模型成本隐藏在原档位名称下。显式 manual 能让高成本探索保留实际身份、配置指纹和预算证据，同时避免扩张 V1 的冻结预算契约。
- 影响：按 D061 路径公式，manual 最多执行 30 轮 Refine，合法最坏路径为 197 步、注入 recursion limit 201，仍低于全局 256 上限；Model Author 的进程级最大 LLM/Refine 包装预算相应为 32/30。浏览器 manual 默认等待上限为 30 分钟，但仍只是客户端停止等待，不提供服务端取消。manual 不得进入冻结 gate，其成本、质量和时延必须单独记录；本决策不改变 Graph 节点、边、路由、`current_best` 安全边界、scorer、Prompt 或 F09 active/no-go。

## D063 - 不接入多尺度 tile 最大回退 guard 的离线 replay 形式

- 日期：2026-07-23
- 决策：不把本轮 `4×4/8×8` 全 tile RGB MAE 最大回退 guard 的 offline replay 形式接入生产 scorer 或候选选择。固定 7 例继续保留 strict total-loss Arm A 和预声明容差 `0/0.001/0.0025/0.005/0.01` 的完整负结果；下一质量增量改为在同一候选预算和 draw 预算下直接运行 geometry-first 字典序与 strict total-loss 两种 live acceptance 的单因素 A/B。
- 原因：strict total-loss Arm A 的两个 watch ROI 没有达到冻结 `0.01` 回退阈值，offline Arm B 因而没有保护收益；较严格容差反而拦截 `color_lobes` 等明确改善，`t≤0.005` 时四例丢失全部改进，`t=0.01` 时 `shadow_disk` 和 `pink_gel` 仍全部拒绝。该证据足以否决当前 offline replay 形式的生产接入，但 Arm B 沿用 Arm A 生成的候选流；拒绝早期候选后 live candidate generation 会改变，不能据此因果性地证伪 live guard，或把两次实验的 ROI 差异直接归属为某一种 acceptance。
- 影响：生产 `min_scene_composite_v3`、Prompt、Graph、预算、目标和 `current_best` 安全边界均不改变，F09 继续 `active/no-go`。该 runner、测试和规格仍直接依赖已由 D066 删除的 V1 benchmark manifest、ROI/Oracle 与 scorer calibration，因此同步 `a39e676` 后在当前重构工作树继续删除；455 次真实 Chromium draw、0 模型调用的本地报告也已在 D067 授权下删除，只由本决策保留负面结论与历史 commit 可追溯性。rim、弧形高光和双高光缺失仍是发布阻塞项，自动代理看片不替代独立人工偏好 gate。

## D064 - 最小骨架重构先解除 V1 共享依赖并删除旧方案源文件

- 日期：2026-07-23
- 决策：以《PNG 转无贴图 GLSL Agent—最小骨架（快速版）》作为当前实施切片。第一波清理删除无运行时消费者的 V1 实现/Prompt 草案和旧 V2–V5 方案源文件；历史取舍继续由本文件和进度归档说明，不删除冻结 benchmark、失败证据或旧 run 数据。最小骨架复用的稳定 JSON/多模态消息构造迁入 `agent.app.messages.structured_multimodal`，通用 WebGL1 运行契约迁入 `shaderforge.contracts.webgl1`；历史 `contract_id=webgl1_static_no_texture_v1` 保持不变。
- 原因：旧方案资料已不再指导当前实现，而最小骨架仍直接导入 V1 消息模块，Renderer、Validator 和测量层也直接导入 V1 业务契约。按文件名直接删除 V1 会破坏当前链路；先建立中立依赖方向，才能让后续 Graph/Service/Node 清理成为可验证的独立增量。
- 影响：架构测试禁止 `png_to_shader_min` 导入 V1 业务命名空间，并禁止通用 analysis/rendering/validation 反向导入 V1 契约。V1 Graph、Service、Node Lab Provider、Memory/checkpoint、Backend/Frontend `procedural_v1` 和历史 Artifact 读取仍是当前消费者，本决策不授权在未确定外围能力与历史数据策略时删除它们。D062 后 high 继续为 `640/9/9`，`1000/32/30` 仅作为独立 manual 档存在。

## D065 - 当前分支的旧 Node Lab 全量退役

- 日期：2026-07-23
- 决策：按用户确认选择 V1 退役计划的方案 B，从当前分支删除旧 Node Lab 的通用 Harness、V1 Provider、Agent/Backend Service、HTTP Route/Schema、Frontend 工作台、CLI、benchmark/fixture、运行配置、打包入口、测试和当前功能项。该实现已在其他分支重新建设，本分支不保留兼容入口或占位模块。
- 原因：旧 Node Lab 不再服务当前最小骨架实施主线，继续维护会让 V1 Provider、独立 benchmark 和调试产品面成为无效耦合，并阻碍后续 V1 可执行链路清理。
- 影响：`H02` 不再是当前功能，原验收命令和环境变量被删除；D028、D032 及 D046 中只针对旧 Node Lab 的部分转为历史。既有 ADR、进度归档和 `docs/evidence/registry.json` 的报告摘要/hash 保留，已生成的本地历史报告不主动覆盖或删除。此决策不授权删除或迁移 Memory/checkpoint、过程账本、M5 benchmark、历史 run 或 V1 默认产品链路；这些仍需独立策略和门禁。

## D066 - 产品直接收敛到 scene_mvp 并删除 V1 可执行链路

- 日期：2026-07-23
- 决策：按用户明确授权，当前分支直接以 `scene_mvp` 最小骨架作为唯一产品路径。删除 V1 Graph/routing/State/Node/Parser/Prompt/Service/业务契约，删除 Backend/Frontend 的 `procedural_v1` 分流、旧 Artifact fallback、项目 Memory API/UI，删除 V1 benchmark manifest、图片、golden、gate、runner、CI、fixture 和对应测试；同时删除只服务 V1 的 TargetMeasurements、Basic Oracle、Selector 与 measurement-affine seed。`langgraph.json` 只注册 `png_to_shader_min`，Generate 请求不再接受 `generation_mode`。此决策取代 D048、D049、D051、D062、D064、D065 中要求继续保留 V1 默认产品链路或旧 benchmark 运行入口的部分。
- 原因：V1–V5 旧方案不再服务当前最小骨架，保留完整可执行链路会继续扩散模式分流、Memory 绑定、旧评分契约和 benchmark 维护成本。最小骨架已经具备独立 Graph、Service、API、进度、Artifact 和 UI 垂直切片，可以作为唯一重构基线。
- 影响：Memory/checkpoint 的 Python/SQL 实现和已有 PostgreSQL 数据暂不删除，但 Backend lifespan、当前 Graph、Service、HTTP 和 Frontend 均不再消费；其迁移、只读归档和保留期需要新决策。过程账本、历史 run、失败证据、`output/benchmarks`、ADR、进度归档和 evidence registry 保留。旧 V1 benchmark 结论只能用于历史审计，不能作为当前 scene_mvp 发布门禁；后续必须建立版本中立的新 benchmark。清理同时移除 `build/`、`.mypy_cache/`、Python `__pycache__`、旧 V2 `.DS_Store` 和其他删除模块缓存。

## D067 - 明确删除本地历史输出与陈旧开发产物

- 日期：2026-07-23
- 决策：按用户针对精确范围的明确授权，删除整个本地 `output/`，包括旧 Node Lab 的运行与 benchmark 证据、V1/V2/M5 benchmark、历史 PNG-to-Shader run、Playwright 截图、review package 和其他本地产物；同时删除 `.pytest_cache/`、`.ruff_cache/`、`shadergen.egg-info/` 与 `frontend/dist/`。本次不制作额外归档。此决策取代 D065、D066 以及更早决策中要求继续保留这些本地 Artifact 的部分。
- 原因：旧 V1–V5 与旧 Node Lab 已退出当前最小骨架主线，用户确认不再需要依赖本地历史产物复盘，并接受未跟踪文件删除后无法从仓库恢复。
- 影响：约 777 MB 本地输出被删除；Git 曾跟踪的少量截图和 review package 仍可从历史 commit 恢复，其余忽略文件只能依赖仓库外备份。`docs/evidence/registry.json` 和进度归档继续保留原结论、路径、字节数与 SHA-256，但对应 evidence 降为 `missing`，不得用于复验旧 gate 或证明当前质量。Memory/checkpoint Python/SQL 实现、PostgreSQL 数据、`.venv/` 和 `frontend/node_modules/` 不在本次范围。

## D068 - Memory/checkpoint 休眠保留

- 日期：2026-07-23
- 决策：按用户最终确认，保留 `src/agent/app/memory/`、`src/agent/app/context/`、Backend Memory 数据库适配与相关 SQL，以及 PostgreSQL 中已有 checkpoint/Memory 数据。当前 `scene_mvp` Graph、Backend lifespan、HTTP API 和 Frontend 继续不接入这套能力。
- 原因：保留实现与数据可以避免在尚未确定 scene_mvp 新 Memory 契约和 namespace 前做不可逆迁移，同时不让旧 V1 语义重新进入当前产品路径。
- 影响：Memory 代码和数据库数据不是待清理残留；未来重新启用必须建立 scene_mvp 专用契约、namespace、保留期和迁移验收，不能直接恢复旧 V1 Service/API。现有 `make setup-memory-postgres` 与 `make test-memory-postgres` 仅用于维护休眠基础设施，不代表在线产品已启用 Memory。

## D069 - 最小 Shader DSL 先以非权威 shadow 纵向切片接入 F09

- 日期：2026-07-24
- 决策：采用“有序 Layer + 层内受限 CSG 树”的 `shader_graph_v1`，由确定性 specialized Compiler 静态展开为 WebGL1 GLSL；公开契约支持最多 8 层、四种 primitive、transform、三种 Boolean、三种 Fill、rim/shadow/glow、opacity 和 source-over。Compiler 默认全参数烘焙，也允许单个 active block 提升为不超过 14 个 packed `vec4` uniform；不同 topology 通过 run-scoped 有界多 program registry 隔离。当前只在 `scene_mvp.finalize` 的产品 best 已冻结后执行一次非权威 shadow：MinScene 可证明子集转换、编译、真实 Chromium 渲染，再把状态、版本/hash、资源计数和只读 graph 写入 manifest/API/UI；产品 GLSL、scorer、render_count、预算、Graph 拓扑和 `current_best` 均不改变。
- 原因：直接把固定四槽扩成 8 层动态图会同时改写 Model Author、Renderer 生命周期、优化地址、API/UI 和质量门禁，也会触碰 WebGL1 最低 uniform 容量。先落确定性契约、Compiler、program cache 和完整纵向 shadow，可以验证架构方向与资源关闭语义，同时把尚无 benchmark 的像素质量风险隔离在产品选择之外。
- 影响：`circle|ellipse`、`solid|linear|radial` 和 `rim|shadow|glow` 可以进入 shadow；旧 radial 从 object-local 椭圆坐标映射到 Canvas radial 时暂用短轴近似。旧 `shadow` 是独立椭圆 footprint，不得误映射为复制整个主体的 SDF ShadowEffect，适配器将其转换为主体后方的独立低 Alpha Layer；该映射仍只用于链路验证，不宣称像素等价。`polar_arc|edge_line|gaussian_lobe` 当前明确返回 `unsupported`，不得静默丢弃或宣称无损迁移。辅助 graph/GLSL/PNG 文件保存在 run 内，但公开 Artifact 白名单仍只有 final-render、metrics、manifest；普通进度事件不携带 Scene、ShaderGraph 或 GLSL。F09 继续是唯一 `active` 功能，F03 不因非权威 shadow 自动变为 active/passing；产品切换、Graph Author/typed layer patch、参数优化与正式质量 benchmark 仍需后续独立决策和证据。

## D070 - F09 默认产品真相源切换为有界 ShaderGraph

- 日期：2026-07-24
- 决策：保持 `png_to_shader_min` 的 12 个 LangGraph 节点、直接边、条件边和终止路径不变，默认组合根把领域表示从 MinScene/固定模板切换为 `ShaderDocument`。Initial Author 输出完整严格文档；Refine 每轮只输出一个绑定 `base_document_sha256` 的 typed layer patch。`current_best` 改为不可变 `ShaderGraphCandidateSnapshot`，绑定文档、Compiler 产物、program key、真实 Render、metric、父文档 hash 与 provenance；Prepared handle 只留在 run-scoped registry。参数优化按稳定 `node:<id>.*` / `layer:<id>.*` 地址和最多 12 个 block 做 current±step 小邻域，保持 strict total-loss 单调接受。`optimize_feature` 与 `feature_queue` 名称暂为兼容既有 Graph 路由保留，但不再表示旧 Feature。
- 原因：D069 已证明 DSL、specialized Compiler、WebGL1 资源规划和多 program cache 可以独立运行；继续让 ShaderGraph 只做 finalize shadow 会形成两套表示、两次渲染和无法进入模型/优化器的架构死角。直接增加 DSL 对应 LangGraph 节点则会把领域 DAG 错当工作流并扩大递归与路由复杂度。沿用宏观闭环、只替换领域真相源，可以用最小改动贯通 Author、Compiler、Renderer、Optimizer、选择与 Artifact。
- 影响：默认 final manifest 升级为 `png_to_shader_graph_manifest_v1`，权威文档写入 `shader_graph`，API 兼容字段 `min_pipeline.scene` 返回该文档，Renderer 路径为 `compiled_graph_program_cache_v1`；旧 shadow runner 仅供显式 legacy Builder 测试和兼容审计。结构 patch 首版只分配一次 raw draw，不沿用旧 MinScene 的 12-draw 局部成熟；数值优化跳过需要成对归一化的 rotation 标量，旋转表达能力仍可由 Author 使用。Graph 最坏路径按最多 12 个参数 block 重新推导，manual recursion limit 为 217，仍低于 256。当前只完成架构与链路 canary，不改变 scorer/目标，不引入 CMA-ES、正式大 benchmark、可视拖线编辑器、异步任务或人工 gate；F09 保持 `active`。

## D071 - Author 与感知边界先收敛，参数优化留待跨分支整合

- 日期：2026-07-24
- 决策：当前分支不继续修改 rotation、成组参数、typed layer patch 局部成熟或更大搜索等参数优化能力，统一登记为跨分支 TODO，待另一分支成果可审查后再决定择入。当前增量只把 ShaderGraph Initial/Refine Prompt 升级为 v1_2，使模型从可靠 fallback 按 Layer 分解并按主导问题选择单个 typed op；`edge_line`、`gaussian_lobe`、`polar_arc` 分别优先用 segment、ellipse+radial Alpha、ellipse CSG 弯月近似表达，不新增专用节点。感知阶段同时产出 legacy MinScene 与产品 `fallback_shader_graph`，产品 Author 直接消费后者；共用迁移映射归入 ShaderForge typed 边界。
- 原因：参数优化已在另一分支并行演进，在当前分支重复修改会扩大冲突且难以归因。三个旧 Feature 的通用表达尚未经过固定质量样例，立即扩展 Schema/Compiler 会把兼容问题升级为新语言设计；Prompt 规则与现有节点足以先验证链路和常见近似。感知转换继续放在 agent shadow 模块也会让默认产品依赖非权威兼容路径。
- 影响：Graph 节点、边、路由、scorer、selection policy、参数 manifest 和优化器均不改变；legacy 迁移遇到三个旧 Feature 仍 fail closed，不宣称无损。满 8 Layer、translate/scale/rotate、三种 Boolean 和层序由一个真实 Chromium/WebGL1 集成用例验证。生产 Qwen v1_2 orb canary 一次生成合法文档但最终仍由 scorer 选择 perception fallback，因此只证明链路完整，不构成质量提升或发布证据；F09 保持 `active`，durable benchmark 与独立人工门禁仍是发布缺口。


## D072 - acceptance live 单因素 A/B 证据支持 strict total-loss，生产 acceptance 暂不改

- 日期：2026-07-23
- 决策：记录固定 7 例 acceptance live 直接 A/B 的结论：在同一候选生成器、参数范围、阶段顺序、每 stage 32 draw 预算和同一 fallback 初始快照下，strict total-loss acceptance 在 6/7 案例的内部 total loss 与外部 `png_to_shader_score_v1` objective 严格优于 geometry-first 字典序（`solid_circle` 两臂轨迹完全相同），两项 aggregate 同时更优，且不复现 geometry-first 的两例实质 ROI 回退（`ellipse_gradient/upper_color +0.019894`、`arc_highlight_orb/highlight_upper_left +0.011853`）。在固定 7 例、当前确定性候选生成与 32+32 draw 搜索契约下，轨迹差异的唯一实验变量是 acceptance，两例实质回退在该契约内可归因于 geometry-first 字典序 acceptance；该归因不外推为真实模型或其他搜索空间下的普遍结论。生产 acceptance、scorer、Prompt、Graph、预算、目标和 `current_best` 安全边界本次均不改变；是否切换生产 acceptance 需要后续独立决策。
- 原因：D063 否决 tile guard 的离线 replay 形式后，遗留问题正是“两次实验的 ROI 差异能否因果归属于 acceptance”。本次 live 直接 A/B 两臂候选数完全相同（各 448 次）、无 offline replay，轨迹分叉只来自 acceptance 谓词；903 次真实 Chromium draw、0 模型调用，机器可读 gate 的 outcome 为 `strict_total_supported`（逐 case 外部 objective 与 ROI 的 0.01 实质回退双向检查、aggregate mean/median 双向比较，输入缺字段显式 fail closed）。权威产物为 `output/diagnostics/scene-mvp/acceptance-live-ab/20260723-v2`（`scene_mvp_acceptance_live_ab_v2` schema），报告 SHA-256 `2daa4c77b274efed7ede863444b4ce6d5141bf92168075f722e7b0ded00cdd11`；output run iteration 与 report schema 是两个独立版本轴，`20260723-v1`（gate 前探索性运行）与 `20260723-v2-schema-v1-superseded`（gate 完整但 schema `_v1`）均标记 superseded 并保留。
- 影响：本决策只固化实验结论与证据身份，不授权生产变更。该实验是 independent no-model diagnostic，不是 D058/D059 冻结 benchmark，不能使 F09 passing；切换生产 acceptance 前仍需真实模型固定 7 例 benchmark 与独立人工偏好 gate，自动代理看片不替代人工盲评。rim、弧形高光和双高光缺失在两臂下均未解决，仍属模板/特征表达能力缺口的发布阻塞项。

## D073 - 生产 acceptance 自始为 strict total-loss，不存在 geometry-first 切换对象

- 日期：2026-07-23
- 决策：记录事实纠正：生产 `scene_mvp` 不存在 geometry-first 字典序 acceptance。全部 acceptance 比较点——`render_and_evaluate` 初始/fallback 择优与 incumbent 提交、`optimize_base`、`optimize_feature`、Refine branch matured 提交（`src/agent/app/nodes/png_to_shader_min/runtime.py`）——自始只按 `min_scene_composite_v3` 的 `total_loss` 严格改善提交；`geometry_mask_loss` 仅作为复合 loss 的加权分量与证据 delta key 出现，从未单独决定接受。D072 Arm G 的 `(geometry_mask_loss, total_loss)` 字典序谓词只存在于诊断脚本（`scripts/run_scene_mvp_acceptance_live_ab.py` 的 `geometry_first_accepts` 与 `scripts/run_scene_mvp_run_diagnostics.py` 的 `_run_geometry_local_search`），其反复 re-propose 加方向交错的搜索循环也与生产单批 rebase 循环不同。因此"把生产 acceptance 从 geometry-first 切换为 strict total-loss"没有对应的生产改动对象；作为防漂移加固，生产五处 total-loss 严格比较统一收口到纯函数 `shaderforge.optimization.accepts_strict_total_loss`，在有效 metric 域内行为与原有内联比较完全等价；唯一差异是 helper 对候选或锚点的非有限/负 loss 显式 fail-closed，该差异只在有效 metric 域之外生效，属防御性契约加固，不是搜索语义变化。
- 原因：D072 后续增量在第一步核对生产调用点时发现，PROGRESS"下一步"与 A/B 报告把 Arm G 表述为"既有/生产 geometry-first acceptance"，与代码事实不符；git 历史显示生产 runtime 只引入过 `geometry_mask_loss` 证据 delta key，从未引入字典序谓词。若不纠正，后续决策会基于一个不存在的切换对象。
- 影响：PROGRESS、FEATURES、A/B 报告与 README 的误导表述同步纠正；新增 `tests/unit_tests/test_scene_mvp_acceptance.py` 聚焦锁定 acceptance 语义（geometry 改善但 total 变差拒绝、total 严格改善即使 geometry 变差接受、total 持平拒绝、非有限/负值 candidate 或 incumbent loss 均 fail-closed、失败候选不污染 incumbent、预算与候选数不变）。D072 的实验结论与证据身份不变，但其含义应理解为"生产既有 strict total-loss 语义优于被测的 geometry-first 诊断语义"，而不是一次待执行的生产切换。Graph 节点、边、路由、终止路径、`current_best` 安全边界、scorer、Prompt、候选生成、参数范围、draw 预算与 high=`640/9/9`、manual=`1000/32/30` 均不变，故 Builder ASCII、Graph Mermaid 与路由表无需变更。F09 继续 active/no-go：真实模型固定 7 例 benchmark 与独立人工盲评仍是发布阻塞项，本决策不替代它们。
- 合并更新（2026-07-24）：D070 将默认产品切换为 ShaderGraph 后，产品仍使用 strict total-loss，但候选比较位于 `shader_graph_runtime.py`，没有复用 legacy MinScene 的 `accepts_strict_total_loss` helper。D073 的 acceptance 语义继续有效，关于“五处比较统一收口”的实现描述只适用于 legacy Builder。

## D074 - scene_mvp 私有 Patch replay bundle v1 只写 run 目录 private/replay/

- 日期：2026-07-23
- 决策：为 `png_to_shader_min` 增加私有 Patch replay bundle v1。`author_refine` 在获得合法 typed Patch 后立即原子写 `private/replay/steps/refine-NNN/patch.json`（schema `scene_mvp_replay_patch_v1`，含完整 typed patch、patch 摘要和 author model/Prompt identity）；`render_and_evaluate` 的 Refine branch 读取该私有 draft 并写同目录 `record.json`（schema `scene_mvp_replay_step_v1`，含 anchor/candidate/raw/matured scene+hash、loss/metrics、render hash 或内容寻址 PNG、maturity proposal 序列、draw before/after、accepted/rejected_reason，duplicate/invalid/renderer_failed/tie 均落记录）；`finalize` 汇总私有 step refs 生成 `private/replay/bundle.json`（schema `scene_mvp_replay_bundle_v1`，含 run identity/config fingerprint/预算、reference hash、model_ref+identity_source、Prompt/scene/template/metric/optimization/selection 版本、metric_background 与完整 steps；源码 revision 无可靠注入点时显式记录 `unavailable`，不 shell 调 git、不伪造）。State 只经两个 `UntrackedValue` 字段（`pending_replay_step`/`replay_step_refs`）传递 path/hash/size 引用；公开 manifest 只新增 `private_replay_bundle={schema_version,sha256,size_bytes,step_count,durability_status:local_ignored}`。`_PUBLIC_ARTIFACTS` 保持 final-render/metrics/manifest，HTTP 白名单、metrics/API/DB 摘要、trace、progress 均不含完整 patch、候选 scene 或私有路径。
- 原因：run `79f51d8a-1aaa-4f92-b806-cd8a44ddf297` 复盘与 task_bb813c983248 审计表明，hash-only 公开证据可判断接受结果，却无法离线重放同一真实 typed Patch（缺 anchor/candidate scene、raw/matured 指标与模型/Prompt 身份），导致 12/32 draw maturity 等单因素重放实验没有生产数据源。完整 patch 与候选 scene 属于私有证据，若进入 trace/progress/DB/公开 manifest 会泄露用户图片语义与模型行为细节，因此必须只落 run 目录私有子树。
- 影响：replay 内容只对未来 run 生效，run 79f 无法追溯补值；`private/` 位于本地 artifact 根，durability 为 `local_ignored`，跨环境复验仍需独立 durable 机制。Graph 节点、边、路由、终止路径、`current_best` 安全边界、scorer、Prompt、候选生成、draw/LLM/Refine 预算与 quality presets 均不变，Builder ASCII、Graph Mermaid 与路由表无需变更；本增量不做 12/32 实验、不改 maturity 12 draw 生产预算。新增聚焦测试覆盖 accepted/tie/duplicate 0 draw/invalid/renderer_failed、bundle hash 与 manifest 摘要一致、公开面不泄露与 HTTP 白名单拒绝 replay 名称。F09 继续 active/no-go：真实模型固定 7 例 benchmark 与独立人工盲评仍是发布阻塞项。
- 同日修订（只读审查跟进）：读回侧改为 fail-closed 校验——step 目录只从 `refine_count` 派生（不信任 State 携带路径），patch draft 与 step record 读取经 `decode_verified_replay_json()` 精确校验预期 `private/replay/` 路径、sha256、size_bytes、JSON object/schema/refine_count；内容寻址 render ref 增加 `size_bytes`/`content_type`，复用已存在文件与 `finalize` 汇总 raw/matured render 时均复验 hash/size；`bundle.json` 同样经 `_write_replay_json_once` write-once 落盘，重复 finalize fail-closed；maturity proposal 的原始 RGB hash 改名 `render_rgb_sha256` 并记录 `render_rgb_encoding`/`render_width`/`render_height`，与 PNG 文件 hash 严格分域；step author 改为 `requested_model_ref`+`model_ref` 与 `source_prompt`(refine)/`output_prompt`(repair 时为 repair prompt) 双字段，且 patch draft 延迟到 apply 之后写盘，patch_apply_failed 时 draft 与 record 的 author.error_code 一致；bundle 顶层模型身份改为从 steps 聚合的 requested/actual 去重列表（`scope=refine_steps_only`），不再只声称尾值。新增测试覆盖 verified 解码各失败分支、路径注入/穿越拒绝、render 复用复验、draft/record/render 篡改 fail-closed、零 step 与多 step bundle、repair 双 Prompt 身份、apply 失败一致性、progress 事件与 final_result 不泄露。生产 scorer、Prompt 文本、Graph 拓扑、路由、`current_best`、quality preset 与 12 draw 预算均未变。
- 恢复边界：当前 write-once 是单 Graph run 的应用层约束，不提供跨进程事务。若进程在 `patch.json` 已落盘而 `record.json` 尚未落盘时崩溃，同一 run_id 重放该 refine step 会因 orphan draft 而 fail-closed；不得覆盖或猜测恢复，须使用新 run_id 重新执行。该缺口不改变公开/私有边界或 `current_best`，后续若要求断点恢复，需另立决策设计 step 状态机与原子提交协议。
- 合并更新（2026-07-24）：D070 已把默认产品真相源切换为 ShaderGraph；本次合并保留 replay 契约、legacy `make_min_nodes` 实现和聚焦测试，但默认 `make_shader_graph_nodes` 尚未写入该 bundle。D074 当前只约束 legacy MinScene Builder 的私有证据边界，不得据此宣称 ShaderGraph 产品已经具备 Patch replay；迁移 typed layer patch replay 需在新契约下另立决策。

## D075 - 12/32 maturity fixture 重放支持继续验证 32，不授权生产改动

- 日期：2026-07-23
- 决策：记录 run 79f 两个冻结合成 feature Patch 的 12/32 draw 单因素真实 Chromium 重放结论。两臂共享同一次 raw，Arm-12 严格执行生产单批 11 local draw，Arm-32 完整复用相同的前 11 次实际 draw 后，以同一参数绑定、步长、边界、固定方向顺序、rebase、`min_scene_composite_v3` 和 `accepts_strict_total_loss` 有界扩展到 31 local draw。两个 fixture 的前 11 次 `(parameter_path,direction,before,after,loss)` 与第 11 draw 后 best Scene/loss 完全一致；87 次 physical draw 与账本相等，0 模型调用、无 final 隐藏 draw。`underfit_top_left` 两臂均接受；`overfit_top_left` 由 Arm-12 的 `0.049148339`（差于 anchor `0.048350444`）在 Arm-32 降至 `0.048101946` 并 clean rescue。预声明 `nonempty_clean_rescue_v1` gate 为 `budget32_supported`，每 rescue 额外 local draw 为 20。
- 原因：D074 已提供未来真实 Patch 的私有重放机制，但历史 run 79f 不能补出 typed Patch；先用既有冻结 fixture 验证 12/32 的实现、前缀、记账和 gate，可把“局部成熟预算可能不足”从推测收敛为小样本机制证据。Kimi 并行只读审计发现原设计的完全对称 gate 在零 rescue 时为空真，且 color-field 绑定数不是固定 5；因此在看结果前把 32 支持条件收紧为至少一个无外部 objective/ROI 实质回退的 non-empty rescue，零 clean rescue 支持保留 12，混合/缺字段/Renderer 失败均 `inconclusive`，color-field 参数继续从生产 model 动态推导。
- 影响：本轮只新增离线诊断脚本、18 个纯单测、local/partial 报告和文档；生产 runtime、`MAX_PATCH_CANDIDATE_DRAWS=12`、scorer、Prompt、Graph、YAML、high=`640/9/9`、manual=`1000/32/30` 与 `current_best` 均不变。Arm-32 因 strict best 和前缀包含关系在内部 loss 上近乎构造性不劣，真正证据强度只来自 non-empty clean rescue 与成本；两个合成 add-feature fixture 不能代表真实模型 Patch、固定 7 例或人工偏好。生产预算继续 no-go，后续只允许在 D074 明确的旧 MinScene 边界内解释该结果；当前 ShaderGraph 必须重新冻结 Patch replay 与质量门禁。权威本地产物为 `output/diagnostics/scene-mvp/maturity-budget-replay/20260723-v2/report.json`，schema `scene_mvp_maturity_budget_replay_v1`，SHA-256 `ed8caf780554ef527935c82e78bbf4926e1b09d0beb0e9df47f3db396021f15d`；`20260723-v1` 仅有 extra-draw 聚合口径错误，标记 superseded 并保留。

## D076 - 旧 Feature 架构停止追加质量实验，后续分支重建 benchmark

- 日期：2026-07-24
- 决策：用户确认后续分支已经针对 Feature 处理逻辑和架构做出调整，因此停止在当前 `png_to_shader_min_scene_v3` Feature 契约上继续运行真实模型 independent experiment、D074 真实 Patch 12/32 重放、旧 Initial/Refine Prompt 优化、固定 7 例真实模型 benchmark 与匿名盲评。D063、D072–D075 的报告、失败事实和机制验证继续只增不改保留，但只能解释旧 Feature Schema、模板、typed Patch 和候选空间，不得外推为后续方案的质量或预算结论。正式 benchmark 不是永久取消；切换后续分支后先完成 Feature Schema、模板、Patch、Prompt、scorer、Graph、预算与证据版本的差异审计，再为稳定的新方案重新冻结 manifest 和质量门禁。
- 原因：旧 Feature 表达能力、Patch 空间和 Prompt 决定模型可提出的结构以及局部搜索可到达的候选；这些核心契约变化后，继续为旧方案购买真实模型调用或扩大 draw 只能生成缺乏外部有效性的过期证据。把旧结果直接迁移到新架构还会混淆版本身份，并可能基于不再存在的候选空间作出 maturity 预算决策。
- 影响：当前分支的生产 runtime、`MAX_PATCH_CANDIDATE_DRAWS=12`、scorer、Prompt、Graph、YAML、high=`640/9/9`、manual=`1000/32/30`、`current_best` 和可运行性均不改变，也不调用真实模型。D074 的私有证据边界、配置身份门禁、draw 记账和 fail-closed 工具可以在新分支审计后选择性迁移，D075 的 `budget32_supported` 不再授权追加旧架构实验或修改新架构预算。合并后的当前产品即 D070 ShaderGraph，必须重新建立对应 benchmark 与独立人工门禁；通过前 F09 继续 `active/no-go`。

## D077 - ShaderGraph program compile 上限必须与 run 预算一致

- 日期：2026-07-24
- 决策：`GraphProgramRegistry` 的存活 cache 容量继续固定为 4，但 compile 硬上限不再固定为 16。产品按当前 run 的合法最坏候选路径推导 `I + 1 + F + R`：启用 Initial 模型时 `I=2`，否则 `I=1`；canvas 与最多 12 个参数 block 占 `1+F`；`R=min(refine_budget,max(llm_budget-1,0))` 表示每轮最多一个结构候选。该值在 run 首次 prepare 时注入并冻结，同一 run 发生预算漂移时 fail-closed。manual `1000/32/30` 的 compile 上限因此为 45。若实现缺陷仍使上限意外耗尽，ShaderGraph 节点把底层 `GraphProgramBudgetError` 转为稳定 `graph_program_budget_exhausted` 候选失败，沿现有失败/终止路径收敛，不再把未分类异常直接冒泡为 HTTP 500。
- 原因：合并后的产品仍沿用 D069 最小纵向切片的固定 16 次 compile 上限，但 D070 已把参数队列扩大到最多 12 个 block，D062 的 manual 又允许 30 轮 Refine。合法路径最多需要 45 次 compile；实际 run `362d2164-3438-4e53-b784-7104d7c269e7` 在约 23.6 秒后于第 17 个新 program 前抛出 `GraphProgramBudgetError`，Backend 只能记录 `internal_pipeline_error` 并返回 500。固定上限与公开质量档位相互矛盾，不能靠提高全局常量或减少用户预算掩盖。
- 影响：Graph 的 12 个节点、直接边、条件边、路由结果、终止路径和 `current_best` 严格改善边界均不改变；render/LLM/Refine 预算、scorer、Prompt、YAML 与 cache 存活容量也不改变。新增纯函数测试锁定 fast/balanced/high/manual 推导值，新增 30 轮 ShaderGraph typed Refine 集成回归，实际 compile 次数超过旧上限 16 后仍以 `bounded_mvp_complete` 正常固化 Artifact；另有聚焦测试锁定意外耗尽的稳定失败码。

## D078 - 无效 ShaderGraph Refine 不得重新建立参数队列

- 日期：2026-07-24
- 决策：`author_refine` 在模型预算耗尽、typed patch 近期重复、解析失败或 apply 失败而没有可渲染候选时，显式设置 `refine_branch_resolved=true`。随后固定边 `author_refine -> materialize_shader -> render_and_evaluate` 保持不变，但 render 节点执行 no-op：复用不可变 `current_best`、不增加 render count、清空 `feature_queue`，再由既有 `decide_after_render -> optimize_base` 路径完成 no-op base 过桥。只有首次 Initial 仲裁后可以建立完整参数 block 队列；合法 Refine 候选仍真实编译渲染并严格选择。
- 原因：原 ShaderGraph 分支只在“合法 patch 已完成真实选择”时设置 `refine_branch_resolved`。模型返回 base hash 不匹配等无效 patch 时，工作 scene 被恢复为 current best，但 pending patch 为空；render 节点因此误入 Initial 仲裁分支并再次填充全部参数 block。真实 high run `04b7b4af-2dd0-495d-9ac6-0b34f1eeca23` 在连续失败 Refine 后重复参数优化，实际节点步数超过配置公式 `9 + 2F + 6R`，最终在 85 步触发 `GraphRecursionError` 和 HTTP 500。
- 影响：Graph 节点、直接边、条件边、路由结果、终止路径、recursion 公式、render/LLM/Refine 预算和 `current_best` 安全边界均不改变；无效 patch 仍消费已发生的模型调用和 Refine 次数，但不消费 render 或参数搜索预算。新增 high 档连续 base-mismatch patch 的完整 Graph 回归，要求模型调用达到档位上限、运行以 `bounded_mvp_complete` 结束、总节点数低于 recursion limit，且 `optimize_feature` 只执行首次参数队列的最多 12 次。

## D079 - 新增 kimi 模型 provider 并复用 openai model family

- 日期：2026-07-26
- 决策：在 `src/agent/app/llms/provider_config.py` 注册 `kimi` provider，凭据和地址使用 `KIMI_API_KEY`/`KIMI_BASE_URL`，默认 base URL 为 `https://api.kimi.com/coding/v1`。`client_factory.py` 的模型系列路由把 `kimi` provider 映射到既有 openai family，不新建独立的 kimi family 模块。
- 原因：按 D013 的 provider/model-family 分层，新增供应商只需扩展 provider 层；Kimi Code API 兼容 OpenAI chat completions 协议，且 kimi 模型不需要 Qwen/GLM 那样的 thinking 参数或特殊响应字段处理，复用 openai family 即可，新建 family 模块属于无差异重复。默认 base URL 采用用户指定的 Kimi Code 订阅端点。
- 影响：`SHADER_GEN_MODEL_NAME` 可写为 `kimi:<model>`；`.env.example`、根 `.env` 和 `README.md` 服务端环境变量清单同步新增 `KIMI_API_KEY`/`KIMI_BASE_URL`；新增 2 个单元测试锁定 kimi env 默认值读取和 `kimi:` ref 到 openai family 的路由。Graph 拓扑、Prompt、预算、scorer 与质量门禁均不改变；默认模型仍为 `dashscope:qwen3.7-plus`，本次未执行带密钥的真实模型调用。

## D080 - kimi 独立 model family 固定 temperature=1，生产默认模型切换到 k3-256k

- 日期：2026-07-26
- 决策：新增 `src/agent/app/llms/families/kimi.py` 独立 model family，取代 D079 的“复用 openai family”。Kimi Code 端点（`https://api.kimi.com/coding/v1`）对 `k3`/`k3-256k` 只允许 temperature=1，family 层忽略调用方温度并固定下发 1；模型名 `k3*`/`kimi*` 前缀和 `kimi` provider 均路由到该 family，family 默认 provider 为 `kimi`。用户已在根 `.env` 把 `SHADER_GEN_MODEL_NAME` 切换为 `kimi:k3-256k` 并填入真实 `KIMI_API_KEY`。
- 原因：D079 基于“无差异兼容”的判断只经离线推导；首次真实连通性验证证明 `k3-256k` 对 temperature=0 返回 400（`only 1 is allowed for this model`），而生产结构化角色按既有决策统一使用 temperature=0，复用 openai family 会使所有生产调用失败。按 D013 的 provider/model-family 分层，端点级参数限制属于 family 兼容差异，应建独立 family 而不是在 openai family 内加 provider 分支。同一验证确认该端点接受 `response_format=json_object` 和 `max_completion_tokens`，JSON mode 与 `max_output_tokens` 路径无需额外适配。
- 影响：生产模型从 `dashscope:qwen3.7-plus` 切换为 `kimi:k3-256k`；结构化角色失去 temperature=0 的确定性语义（端点强制 1），这是切换的固有代价，不通过伪造参数掩盖。Graph 拓扑、Prompt、预算、scorer 与质量门禁均不改变；`.env.example` 默认模型仍为 `dashscope:qwen3.7-plus`。既有 Qwen 质量证据不得外推到 k3-256k，按 D076 需在新模型上重新建立 benchmark 证据。单元测试锁定 family 温度固定、env 默认值和 `kimi:`/裸模型名两条路由；已通过真实调用验证文本、JSON mode 和 `max_output_tokens` 路径。

## D081 - kimi thinking effort 经环境变量下发，默认 low

- 日期：2026-07-26
- 决策：`families/kimi.py` 新增 `reasoning_effort` 支持，取值 `low/high/max`，由 `SHADER_GEN_KIMI_REASONING_EFFORT` 环境变量控制，默认 `low`；非法值在客户端侧 fail-fast。`.env.example`、根 `.env` 和 `README.md` 清单同步新增该变量。
- 原因：真实行为实验证明 Kimi Code 端点接受并执行 OpenAI 兼容的 `reasoning_effort` 参数（同一问题 reasoning tokens：缺省 70、low 56、max 221），但端点对非法值静默忽略，所以必须在 family 层校验。用户要求先把生产 thinking 固定为 low；中立 `thinking` 契约只有 default/on/off 三态，装不下 low/high/max，暂不经 `LLMCallOptions` 暴露，保持 env 单点控制。
- 影响：生产 `kimi:k3-256k` 调用默认以 `reasoning_effort=low` 运行；Graph 拓扑、Prompt、预算、scorer 与质量门禁均不改变，D080 的温度固定不变。单元测试锁定默认 low、env/参数覆盖和非法值校验；已通过生产路径真实调用确认 effort 随客户端下发。未来如需 node 级 effort 控制，须先扩展中立 thinking 契约再接入 family。

## D082 - 通用 Node Lab 向前移植为独立开发工具，不恢复旧 V1 插件

- 日期：2026-07-26
- 决策：将 `origin/codex/refactor-node-lab-generic@222ea96` 的 Pipeline 无关 `nodelab` 内核、独立 `nodelab_service`、受信任 Application factory 和 `/lab` 工作台向前移植到当前 `main`。独立服务默认创建空安全 Application，产品 Backend 不注册 `/api/lab/v1/*`。冲突中继续采用当前 `main` 对旧 PNG-to-Shader V1 Graph、Agent Adapter、benchmark manifest、脚本与专用测试的删除结果。
- 原因：目标分支基于旧 V1 架构开发，直接接受全部 modify/delete 冲突会重新开放 D076 已退役且当前代码无法支撑的运行入口；完全采用 `main` 删除结果又会丢失已经完成的通用 Harness、独立部署边界和工作台。以受信任 factory 作为唯一领域注入点，可以保留通用能力而不让 transport 反向依赖 Agent 或 ShaderForge。
- 影响：新增 `make dev-node-lab`、`NODELAB_*` 服务端配置、`VITE_NODE_LAB_API_BASE_URL` 和 `/lab` 页面；`nodelab`/`nodelab_service` 随 `shadergen` distribution 发布。若未来需要调试当前 `png_to_shader_min`，必须为 ShaderGraph 现契约另建 Provider/Executor factory，不得复活旧 V1 Adapter 或引用已删除的 benchmark 证据。F09 的产品 Graph、路由、`current_best`、质量门禁和历史 evidence registry 均不改变。

## D083 - LayerPlan + 直接 GLSL Author 仅以非权威 shadow 实验接入，第一阶段只交付修订版设计基线

- 日期：2026-07-26
- 决策：按用户确认的语义建立四层真相层级：参考图是视觉真相，LayerPlan 是由独立受约束视觉分析 Author 直接读取参考图生成的非权威视觉分层参考（永久 advisory，不参与 scorer/acceptance），ShaderProgramSpec 是模型生成并经安全校验的执行真相，真实 Render/metric 是选择真相。本决策只授权 shadow 实验：第一阶段只交付修订版设计基线 `docs/superpowers/specs/2026-07-26-layerplan-glsl-shadow-design.md`，其中定义 LayerPlanV1、ShaderProgramSpecV1、ShaderCandidateSnapshotV2 契约、Author 角色与时序、校验安全边界、Artifact/API 兼容、shadow A/B 预算隔离与晋升门禁；不修改生产 Graph、代码、API、FEATURE 状态、scorer、预算或 `current_best` 安全边界，不删除或覆盖任何历史证据。被 Codex 审阅否决的首稿（旧 `docs/LAYERPLAN_SHADOW_DESIGN.md`）已随修订移除，不作为任何基线。
- 原因：生产 `kimi:k3-256k` 的 Initial 仍常由 scorer 判定输给 perception fallback（D080/F09 缺口），视觉分层参考可能改善 Author 的结构起点，但尚未有任何证据。首稿把 ShaderProgramSpec 错误定义为 D070 编译产物的派生身份、保留 document/compiled 双真相、且未让 Author 直接读取参考图，审阅不予验收；修订稿按上述语义重写。D069 的经验表明，不经门禁就把第二表示接入产品会形成双真相和架构死角，因此候选选择仍只能由真实 Render 与 strict total-loss 决定。
- 影响：shadow 期间 D070 生产路径完全不变：`png_to_shader_min` 继续是 12 节点闭环，ShaderDocument/specialized Compiler/Renderer/CandidateSnapshot 链路是默认执行真相；legacy ShaderDocument 路径在 shadow 实验中只能作为带显式 provenance 的 control/fallback，任何失败不得冒充 model-generated。只有第 10 节晋升门禁（含 durable、内容寻址、可跨环境复验证据；`local_ignored`/`partial` 只能 no-go）全部通过后的新 ADR，才可以取代 D070 的执行表示部分。LayerPlan 在任何阶段都不得直接决定候选接受；后续实现（shadow harness、A/B、晋升）必须另立决策并满足设计文档中的预算隔离与晋升门禁，晋升前 F09 的发布缺口不变。第一阶段只改文档，不改 `langgraph.json`、Builder ASCII、Graph Mermaid 或路由表；设计文档中的契约在对应实现决策落地前不构成交付承诺。

## D084 - 以独立 harness 实现 LayerPlan/direct GLSL shadow A/B

- 日期：2026-07-27
- 决策：按 D083 的修订设计实现第二阶段最小 shadow 能力：新增独立的 LayerPlanV1、ShaderProgramSpecV1、可信规范化/哈希/校验凭据契约，新增直读参考图的 VisualAnalysis、InitialGLSL、RefineGLSL Author，并用独立 harness 执行 Arm A（不提供 LayerPlan）与 Arm B（只增加同一 LayerPlan）。两臂固定同一模型、Prompt 主体、采样参数和 direct Author 预算，各自拥有隔离的调用、token、compile、draw、wall-clock、program cache 与 `current_best` 记账；VisualAnalysis 使用独立 `plan_llm_budget/PlanLedger`，不得减少 Arm B 的 Initial/Refine/repair 预算。LayerPlan 永久 advisory，接受谓词只能读取真实 Render 与 metric。详细产物只写显式指定的本地私有 run 目录，报告记录内容 hash、arm identity 和执行顺序，但本阶段不登记为 durable 发布证据。
- 原因：D083 已冻结语义和安全边界，但没有可运行代码，无法验证“LayerPlan 是否真的改善 direct GLSL Author”。直接修改 D070 生产 Graph 会在尚无证据时制造双真相；独立 harness 可以先验证契约、模型输入、GLSL 安全校验、真实渲染和 A/B 隔离，同时让失败保持在实验域。
- 影响：`png_to_shader_min` 的 12 节点拓扑、`langgraph.json`、Backend/API、公开 Artifact 白名单、scorer、产品预算及 D070 `current_best` 均保持不变；本次不增加产品路由，也不把 shadow 结果写入生产 manifest。ShaderProgramSpec 不得从 `CompiledDslShader` 或 `GraphProgramKey` 派生；模型不得提供 attestation/hash，可信层重算 hash，只有经过静态校验及真实 WebGL1 compile/link/draw 后签发且匹配的 attestation 才允许形成候选。实现和离线测试通过只证明 harness 可运行，不证明质量晋升；真实模型 A/B、durable 内容寻址证据、跨环境复验和人工偏好门禁仍需后续执行，任何生产晋升必须另立 ADR。
- 修订（2026-07-27，Codex 独立审查高风险项收口）：调用身份改为事实制——`LLMResponse.effective_identity` 由 Gateway 按 family 实际下发参数记录（kimi 强制 temperature=1 并以 `SHADER_GEN_KIMI_REASONING_EFFORT` 承载 thinking），`author_identity.sampling_params` 只写 effective 值，真实响应缺有效身份时 shadow fail-closed（`author_identity_unavailable`），不得记录 unknown 或请求假值；配置中的 `requested_sampling_params` 仅审计用。输入身份强化：Initial/Refine/LayerPlan 哈希绑定 reference content_type，Refine 另绑定 current_render 内容哈希与 canonical 评估上下文（mae/loss/metrics/residual 哈希 + metric version + preprocess 事实），LayerPlan 绑定 instruction 与实际采样身份。私有证据改为同根 staging + 原子 rename（目录 0700、文件 0600、拒绝 symlink/覆盖），并提供 `verify_shadow_run`/CLI `--verify` 复验全部文件与报告哈希。有效性措辞冻结：无 seed 且 temperature=1 的单次 A/B 只具探索性，任何结论必须多轮重复并做 AB/BA 交叉平衡，单 run 不得声称 LayerPlan 是唯一因果变量；报告显式记录 metric version、preprocess、background 与候选 metric/residual 哈希。
- 修订（2026-07-27，最终安全复核）：receipt capability 拆成 Renderer 私有 signer 与公共 verify-only verifier；公共 `shaderforge.program_spec` 不导出生产签发入口，receipt 必须绑定具体 Spec，候选还要求 PNG hash 与关键 runtime metadata。ProgramSpec 禁止宏定义等 token 改写型预处理指令，关闭 `#define LOOP for` 绕过循环上限；renderer 的 prepare/prepared draw/legacy render 均使用有界超时，worker reset 不再调用可能挂起的 `closePrepared`。结构修复结果通过 `repair_context_sha256` 绑定 repair Prompt、首轮输出、校验错误、Schema 以及首轮与第二次实际调用身份；私有 evidence verifier 只接受规范 POSIX 相对路径、验证 resolved containment，并拒绝证据树中的任意 symlink（含 dangling link）。

## D085 - 前端运行可观测性收敛为单一可测试阶段视图模型，不伪造后端未提供的进度

- 日期：2026-07-27
- 决策：scene_mvp 运行面板的状态推导收敛到 `frontend/src/runStages.ts` 的纯函数 `buildRunViewModel`。它把 `/api/shader/runs/{run_id}/progress` 的白名单事件与快照映射为 pending/running/succeeded/failed/unknown 运行状态、12 节点的已完成/失败/待执行事实、预计下一节点、Graph 事件累计、trace 摘要、路由与停止原因、失败定位、预算和 current_best 质量进度。事件只在节点完成时发出，因此 `next_action` 或数组顺序只能展示为“预计下一节点（未确认开始）”，不得标成执行中或以 `snapshot.current_node` 冒充活动节点。`author_initial` trace 的 `author_source` 只说明 Initial Author 输出来源，不代表最终 current_best provenance；候选基于参考图约束生成并经真实渲染/评分选择 current_best，最终 GLSL/Render 来自冻结 current_best 的 typed ShaderGraph 编译产物。`render_seq` 只作为实时帧刷新序号；预算 used 缺失保持未知；只有真实 `elapsed_ms` 才显示 Graph 事件累计，缺失时保持未知，不推算完整 run 结束时间。
- 原因：旧实现把阶段、计时和标签推导散落在组件内，无法单测，还会把推测的下一节点误报为正在执行。后端不提供节点开始事件或精确百分比，前端不得制造这些事实；结构化文档的命名和 `author_source` 也不能替代参考图、真实渲染、评分与 current_best 选择链。
- 影响：只改前端与 `make check` 接线（新增 `npm run test`，vitest）；进度轮询保持 single-flight，对失败和连续 pending 使用 capped backoff，每次 GET 有独立超时。POST 结算（含停止等待/超时）后继续有界观察，直到服务端终态、明确请求拒绝、新 run、页面卸载或观察上限。产品 Graph、Backend/API、进度事件契约、Artifact 白名单和 `current_best` 均不变。后端仍是阻塞式 API、单进程内存注册表（重启即失、无历史 run 查询），也没有节点开始事件、完整 run 时长、最终候选 provenance 或 cancel；这些缺口由文档明确保留。

## D086 - 冻结 LayerPlan shadow suite 协议后才运行真实 A/B

- 日期：2026-07-27
- 决策：为 D084 的单样本 runner 新建独立 suite 协议。`benchmarks/layerplan_glsl_shadow/manifest_v1.yaml` 冻结 `solid_circle`、`ellipse_gradient`、`rimmed_disk`、`pink_gel` 四个版本中立参考像素、统一 instruction、两轮 `AB/BA` 交叉顺序与两臂共享预算；`gate_v1.yaml` 在查看结果前绑定 manifest 文件 SHA-256、两种臂序的完整 `ShadowABConfig` 指纹、`min_scene_composite_v3`、配对改善阈值、顺序效应、inconclusive 计负、人工偏好与 durable 晋升要求。加载器对未知字段、路径、instruction/reference hash、臂序、配置指纹和 gate/manifest 绑定全部 fail-closed。
- 原因：单次无 seed、temperature=1 的 A/B 只能证明 harness 可运行，不能隔离顺序或服务端漂移；如果在看到结果后再选样本、预算、顺序或阈值，结论不可审计。旧 V1 benchmark 已退役，但其参考 PNG 是与候选表示无关的固定视觉输入；本决策只把四张像素以新路径和新 hash 链纳入 LayerPlan/direct GLSL 新候选空间，不恢复旧 manifest、runner、golden、Feature/DSL 结论或产品入口。
- 影响：本步只交付冻结 manifest/gate、四张固定样本、严格加载器和无模型单测；真实模型调度、跨 run 聚合、suite 报告、人工盲评包与 evidence registry 登记在后续小步实现。生产 Graph、API、scorer、预算、ShaderDocument/Compiler、`current_best` 和 F09 状态不变；即使自动 gate 通过，没有人工偏好和 durable 跨环境证据也只能保持 no-go。
- 实现更新（2026-07-27）：同一协议冻结后新增 `run_layerplan_glsl_shadow_suite.py` 与 suite service，按 manifest 顺序执行全部样本/轮次；每个 run 必须先通过原 D084 verifier 且与 sample/reference/instruction/config/order 精确绑定，随后才按配对 `B-A`、样本中位数、AB/BA 方向一致性和 inconclusive 计负聚合。suite 报告采用私有 staging + 原子 rename、0600/0700、内容寻址目录，并可递归复验全部引用 run；自动结论只可能是 `no_go_automatic_gate_failed` 或 `no_go_pending_human_and_durable`。人工盲评包、durable registry 与生产晋升仍未实现。

## D087 - 首轮真实 LayerPlan suite 自动 gate 失败，生产保持 no-go

- 日期：2026-07-27
- 决策：接受 D086 冻结 suite `shadow-suite-43a0748fa395` 的自动结论 `not_supported`，生产决策为 `no_go_automatic_gate_failed`。本轮不生成晋升用人工盲评、不登记为 durable 证据、不修改 D070 ShaderDocument/Compiler、Graph、scorer、预算或 `current_best`。下一实验增量先提高 Arm A/B 共用 direct GLSL Initial/Refine/repair 对 `webgl1_static_no_texture_v1` 的遵循稳定性；不得放宽 Validator、静默修补越权 GLSL或把 LayerPlan 直接接入接受谓词。Prompt/repair/实现变化后必须升级版本并重新冻结 manifest/gate/实现身份，不得覆盖 v1 run。
- 原因：报告与 8 个单 run 已递归复验，suite SHA-256 为 `43a0748fa39525b0c44106b2ffc323557e29fc1cb553300cb60408af39ee1075`。Arm B 在 5 个可比较 run 中 4 胜 1 负，成功率 `7/8` 高于 Arm A 的 `5/8`，AB/BA 可比较子集方向均有利于 B；但 `solid_circle`、`ellipse_gradient`、`pink_gel` 各至少一轮因 `glsl_renderer_contract_violation` 无法配对，inconclusive ratio=`0.75` 超过 `0.25`，只有 `rimmed_disk` 两轮可比较，改善样本比例=`0.25` 低于 `0.75`。该信号值得后续验证，但不能越过预声明 gate，也不能在无 seed、temperature=1 下宣称 LayerPlan 是唯一因果变量。
- 影响：完整安全摘要写入 `docs/analysis/layerplan-shadow-suite-43a0748fa395-2026-07-27.md`；详细 Spec/GLSL/render 继续只留本地私有目录，durability 为 `local_private_not_registered`，不能支撑跨环境晋升。自动 gate 未通过，人工偏好阶段当前无晋升价值；先完成版本化契约稳定性改造和新冻结实验，再决定是否进入盲评与 durable evidence。

## D088 - direct GLSL v2 先提高两臂共用契约稳定性

- 日期：2026-07-27
- 决策：新增 `direct_glsl_initial_v2_1`、`direct_glsl_refine_v2_1` 与 `min_author_repair_v2_1`，只供 LayerPlan shadow 的 direct Initial/Refine 使用；生产 scene_mvp 与 VisualAnalysis Author 继续默认使用原 repair v1。Parser 保留可信层生成的 probe Spec 并复用完整 `validate_program_spec_safety`，在结构 repair 前覆盖预处理指令、规范循环/1024 上限、资源上限与既有 WebGL1 规则；顶层错误码保持 `glsl_renderer_contract_violation`，次级诊断仅为去重保序、最多 12 条的固定 `code + line|null`。repair v2 只接收固定 required declarations、按错误码映射的指令和安全类别，修复上下文哈希绑定实际 repair Prompt、hints、Schema、原输出哈希和两次有效调用身份。
- 原因：D087 的 3/4 inconclusive 主要来自两臂共用 direct GLSL 契约遵循不稳定；若只改 LayerPlan 臂或放宽 Validator，会污染 A/B 控制变量并掩盖模型失败。把 runner 已执行的 canonical safety 前移到 Author Parser，才能让同一次有界 repair 处理真实阻塞规则，同时不引入确定性 GLSL 静默改写。
- 影响：生产 Graph、API、ShaderDocument/Compiler、scorer、预算和 `current_best` 不变；两臂仍使用相同 direct Prompt/repair/调用参数，唯一预期差异仍是 LayerPlan advisory 输入。shadow 编译失败事件同步移除原始 compiler/link log 与 Validator message，只保留日志存在性、SHA-256 和安全违规类别。v1 Prompt、suite 和真实证据继续保留；下一步必须另行冻结绑定 v2 实现身份的 manifest/gate，验证前不得声称质量改善或晋升生产。
