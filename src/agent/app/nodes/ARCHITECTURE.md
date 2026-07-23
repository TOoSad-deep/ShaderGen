# Nodes 架构

`src/agent/app/nodes/` 按对外 Pipeline/版本组织生产 LangGraph Node 工厂及其支持实现。一个 Node 执行一个明确任务，并返回 partial State；只有已经被两个以上 Pipeline 复用且契约真正中立的实现，才允许提升为根级公共模块。与条件边共享规则的纯 decision callable 仍由 `graphs/*_routing.py` 管理。

## 当前 Node 命名空间

- `png_to_shader_v1/__init__.py`：当前唯一产品 Pipeline 的稳定公开入口；Graph 只从这里导入生产 Node 工厂和运行时协议。
- `png_to_shader_v1/model/`：VisualAnalysis、三模式 ShaderAuthor、VisualCritic、结构化输出和有界模型预算包装器。它只依赖 Gateway/业务契约，不运行 Renderer、Evaluator、Selector 或 Artifact Store。
- `png_to_shader_v1/deterministic/`：Context、运行准备、候选物化、证据校验、真实 WebGL1 渲染、确定性评分、current_best 选择/复核、finalize 和策略晋升。
- `png_to_shader_v1/integrations/node_lab/`：V1 Node 向通用 Node Lab 暴露的 Provider；`registry.py` 维护 descriptor，`deterministic.py` / `model.py` 只做 Lab JSON/Artifact 与生产 callable 的边界适配。Node Lab 内核不导入具体 Node。
- `png_to_shader_min/`：`scene_mvp` 的 12 节点运行时与 Model Author helper；Initial 生成完整 scene，Refine 只从 `current_best` 派生单个 typed patch。Refine 同时接收确定性 worst-tile signed residual、active feature 和最近拒绝摘要；非重复合法 Patch 在独立 branch 内使用最多 12 次总 draw 做范围受限成熟，matured candidate 严格改善才提交。

根目录 `nodes/__init__.py` 不导出 V1 实现，也不保留“看起来通用、实际绑定 V1 契约”的兼容模块。详细模块职责见 `png_to_shader_v1/ARCHITECTURE.md`。

## Node 规则

- Node 通过构造参数接收 `agent.app.contracts.llm.LLMGateway`。
- Node 不得直接依赖 `agent.app.llms`、provider 配置或 model-family 实现。
- Node 负责 Prompt 选择、LangChain 消息组装、Parser 调用、可观测性策略和 partial State 映射。
- Gateway 负责客户端创建、模型调用、耗时、reasoning 提取、usage 和真实模型身份。
- State 和 `model_calls` 中的模型名只使用 `LLMResponse.model_ref`。
- V1 三个结构化角色默认 `temperature=0`、`thinking=off`、`capture_reasoning=false`、`response_format=json_object`；JSON 修复沿用同一请求模型和 JSON mode，并再次强制关闭 thinking。
- JSON/契约失败最多允许一次 Gateway 修复；M3 剩余 model budget 只有一次时禁止修复。VisualAnalysis 的 `regions_of_interest[*].purpose` 若全部错误都只属于显式别名表，可先在本地归一化并重新执行完整严格 Parser；该路径记录 `visual_analysis_roi_purpose_alias_v1`、字段路径和源错误码，不放宽公共 Parser，不猜测未知值，也不消耗第二次模型调用。预算内最后一次失败抛出带已有安全审计、但不含原始响应的明确错误。合法的单个 JSON fence 在本地解析，不消耗修复调用。
- `scene_mvp` Model Author 同样最多追加一次结构修复，但只接受裸 JSON；调用、修复、解析或 patch 应用失败均收敛为 fallback/current_best，不保存原始响应，也不把失败候选写入 best。Patch trace 只允许 operation、feature id/type、规范 SHA-256、metric delta、拒绝原因、重复标记和节点耗时；禁止持久化完整 Patch、图片、Scene diff、GLSL、用户输入、模型原始响应或 reasoning。
- `agent.png_to_shader` logger 记录 run/project、模型阶段、剩余调用/时间预算、模型累计延迟、Renderer/静态校验/评估失败和 finalize 摘要；禁止打印图片、完整 GLSL、reasoning、供应商原始响应和密钥。
- 模型阶段 cap 为 VisualAnalysis 60 秒、Initial Author 120 秒、compile repair 60 秒、Critic 45 秒、visual refine 90 秒，并为下游保留总 wall-time 的 10%、最多 30 秒。Renderer、Evaluator 和 3 秒 bounded close 不得消耗或覆盖已有可返回 best 的事实。
- 编译器日志可能回显源码，只能写入私有 compile Artifact；Graph event 只保留字符数、SHA-256、行号和安全错误码。常量倒序 `smoothstep` 是确定性意图修复，不是对 GLSL 未定义行为的语义等价证明，修后必须重新跑完整 Validator。
- `prepare_measurement_seed` 只消费规范化 RGB PNG 与 `TargetMeasurements`，通过 `shaderforge.public.build_measurement_affine_seed` 生成一次独立根候选；它不读取 case id、benchmark manifest、gate 或 golden Shader，不消耗模型/视觉迭代预算，且仍必须通过完整 Validator、真实 WebGL、Oracle 和 Selector。被拒绝的 seed 不计入模型 stagnation，编译失败也不得消耗模型 repair 预算。
- Candidate provenance 必须精确标记 `origin=model|deterministic`；确定性候选还必须绑定 `generator_version`。Critic 的每轮 Review 写入递增路径，禁止覆盖上一轮诊断证据。
- `model_calls` 同时记录 requested model、Gateway 返回的实际 model、身份来源、输出格式、Prompt/repair 版本、attempt、usage、输出 hash、解析状态和最多 20 条安全 validation issue；Candidate provenance 额外记录 GLSL hash。
- Node 不决定全局流程，不持有数据库连接，不在原地修改 State。
- Node factory 返回的 callable 同时是 Graph 与 Node Lab 的单节点调用 API。输入证据不变量（Author/provenance/GLSL、Candidate/hash、reference/measurements、best Artifact）必须由 Node 校验；Node Lab 只能适配 JSON/Artifact 形状，不能维护更严格或更宽松的平行业务规则。
- V1 Graph 通过 `nodes/png_to_shader_v1/__init__.py` 使用稳定工厂；`model/` 和 `deterministic/` 的内部阶段 helper 不是 Graph Node。新增内部模块时必须纳入 Node Lab 递归源码指纹和架构边界扫描，不保留旧根级兼容入口。
- 新增生产 Graph Node 时，同步在同一功能命名空间的 `integrations/node_lab` 登记 descriptor 和 binding，但禁止修改 `agent.app.lab` 或 Node Lab Service。只消费/返回 JSON-safe State 的 Node 优先用 `DirectNodeExecutor`；只有需要大对象 Artifact 化、依赖注入或输出安全投影时才写专用 Adapter。Graph 节点集合与 Provider descriptor 的一致性属于必跑测试。
- 策略晋升同时暴露无副作用的生产 preview Node；真实 Graph 晋升与 Node Lab preview 共用同一份已验证计划构造，不允许在 Lab 内重写摘要或门禁。
- 两个以上 Node 复用的消息 helper 放入 `app/messages/`；reasoning 日志策略放入 `app/observability/`。
- 不新增把分析、生成、测试和优化混在一起的 `mega_agent_node`。

## 与其他模块的边界

- LLM 抽象从 `app/contracts/` 获取。
- Prompt 主体从 `app/prompts/` 加载。
- 纯输出解析由 `app/parsers/` 完成。
- 图流转和具体 Gateway 装配由 `app/graphs/` 决定。
- `model/` 中的 M2 角色 Node 仍保持可注入、可单测和不依赖 M1 事实层；M3 通过独立 Graph 的预算包装器与 `deterministic/` Node 装配它们，不把 Renderer/Oracle/Store 反向塞进角色 Node。目录归属统一为 V1 不改变这一依赖边界。
- Backend 负责 persistence 生命周期；Node 只通过 Runtime Store 抽象读取/写入，不持有连接池。
