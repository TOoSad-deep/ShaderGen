# Nodes 架构

`src/agent/app/nodes/` 只保存主要 LangGraph Node 工厂。一个 Node 执行一个明确任务，并返回 partial State。

## 当前 Node

- `model_node.py`：`make_model_node(gateway)` 创建基础对话 Node，并把 Runtime Context 映射为 `LLMCallOptions`。
- `generate_glsl_node.py`：`make_generate_glsl_node(gateway, config)` 根据原图生成 GLSL。
- `review_render_node.py`：`make_review_render_node(gateway, config)` 根据原图、渲染图和 GLSL 生成评审结果。
- `prepare_context_node.py`：从 Runtime Store 读取候选 Memory 并调用纯 GSSC Builder。
- `promote_memory_node.py`：把结构化 Review 幂等晋升为项目长期 Memory。
- `visual_analysis_node.py`：调用 VisualAnalysisAgent，把参考图和确定性测量解析成严格 `VisualAnalysis`。
- `shader_author_node.py`：以统一工厂实现 initial、compile_repair、visual_refine 三种受限 Author 模式，并输出完整 GLSL 与 Candidate provenance。
- `visual_critic_node.py`：先验证 candidate/GLSL/render 绑定，再输出只含诊断的严格 `VisualReview`。
- `structured_output.py`：复用“一次语义调用 + 受限本地归一化 + 最多一次 JSON 修复”策略；记录安全字段路径/错误码和输出 hash，不保留原始失败输出。
- `bounded_model_node.py`：M3 角色 Node 的共享预算包装器，按剩余 model budget 限制 JSON repair，并用角色级 timeout cap 与下游 reserve 约束模型调用；已知供应商/结构化失败可安全降级，未知内部异常必须向上抛出。
- `png_to_shader_v1/`：V1 确定性 Node 包。公开入口复用同一批 Graph/Node Lab 工厂，内部按运行准备、候选物化、校验、真实 WebGL1 渲染、确定性评分、current_best 选择/复核和 finalize 职责拆分；详细边界见 `png_to_shader_v1/ARCHITECTURE.md`。Evaluator 会在不覆盖确定性测量 ROI 的前提下追加 `VisualAnalysis` 语义 ROI；Evaluator 不可用时可从真实 WebGL 已验证候选生成明确的未评分 fallback。
- `promote_validated_strategy_node.py`：只把经过 Renderer、Oracle 和 Selector 验证的 current_best 策略晋升 Memory。
- `integrations/node_lab/`：生产 Node 向通用 Node Lab 暴露的公共 Provider；`registry.py` 维护 descriptor，`deterministic.py` / `model.py` 只做 Lab JSON/Artifact 与生产 callable 的边界适配。Node Lab 内核不导入该包的具体实现。

## Node 规则

- Node 通过构造参数接收 `agent.app.contracts.llm.LLMGateway`。
- Node 不得直接依赖 `agent.app.llms`、provider 配置或 model-family 实现。
- Node 负责 Prompt 选择、LangChain 消息组装、Parser 调用、可观测性策略和 partial State 映射。
- Gateway 负责客户端创建、模型调用、耗时、reasoning 提取、usage 和真实模型身份。
- State 和 `model_calls` 中的模型名只使用 `LLMResponse.model_ref`。
- V1 三个结构化角色默认 `temperature=0`、`thinking=off`、`capture_reasoning=false`、`response_format=json_object`；JSON 修复沿用同一请求模型和 JSON mode，并再次强制关闭 thinking。Legacy 自由文本节点仍可使用模型内部 thinking，但默认 `capture_reasoning=false`、`print_reasoning=false`；只有显式 opt-in 才允许 reasoning 进入专用审计列。
- JSON/契约失败最多允许一次 Gateway 修复；M3 剩余 model budget 只有一次时禁止修复。VisualAnalysis 的 `regions_of_interest[*].purpose` 若全部错误都只属于显式别名表，可先在本地归一化并重新执行完整严格 Parser；该路径记录 `visual_analysis_roi_purpose_alias_v1`、字段路径和源错误码，不放宽公共 Parser，不猜测未知值，也不消耗第二次模型调用。预算内最后一次失败抛出带已有安全审计、但不含原始响应的明确错误。合法的单个 JSON fence 在本地解析，不消耗修复调用。
- `agent.png_to_shader` logger 记录 run/project、模型阶段、剩余调用/时间预算、模型累计延迟、Renderer/静态校验/评估失败和 finalize 摘要；禁止打印图片、完整 GLSL、reasoning、供应商原始响应和密钥。
- 模型阶段 cap 为 VisualAnalysis 60 秒、Initial Author 120 秒、compile repair 60 秒、Critic 45 秒、visual refine 90 秒，并为下游保留总 wall-time 的 10%、最多 30 秒。Renderer、Evaluator 和 3 秒 bounded close 不得消耗或覆盖已有可返回 best 的事实。
- 编译器日志可能回显源码，只能写入私有 compile Artifact；Graph event 只保留字符数、SHA-256、行号和安全错误码。常量倒序 `smoothstep` 是确定性意图修复，不是对 GLSL 未定义行为的语义等价证明，修后必须重新跑完整 Validator。
- `prepare_measurement_seed` 只消费规范化 RGB PNG 与 `TargetMeasurements`，通过 `shaderforge.public.build_measurement_affine_seed` 生成一次独立根候选；它不读取 case id、benchmark manifest、gate 或 golden Shader，不消耗模型/视觉迭代预算，且仍必须通过完整 Validator、真实 WebGL、Oracle 和 Selector。被拒绝的 seed 不计入模型 stagnation，编译失败也不得消耗模型 repair 预算。
- Candidate provenance 必须精确标记 `origin=model|deterministic`；确定性候选还必须绑定 `generator_version`。Critic 的每轮 Review 写入递增路径，禁止覆盖上一轮诊断证据。
- `model_calls` 同时记录 requested model、Gateway 返回的实际 model、身份来源、输出格式、Prompt/repair 版本、attempt、usage、输出 hash、解析状态和最多 20 条安全 validation issue；Candidate provenance 额外记录 GLSL hash。
- Node 不决定全局流程，不持有数据库连接，不在原地修改 State。
- Node factory 返回的 callable 同时是 Graph 与 Node Lab 的单节点调用 API。输入证据不变量（Author/provenance/GLSL、Candidate/hash、reference/measurements、best Artifact）必须由 Node 校验；Node Lab 只能适配 JSON/Artifact 形状，不能维护更严格或更宽松的平行业务规则。
- V1 确定性 Node 通过 `nodes/png_to_shader_v1/__init__.py` 暴露稳定工厂；内部阶段 helper 不是 Graph Node。新增内部模块时必须纳入 Node Lab 递归源码指纹和架构边界扫描，不保留旧大文件兼容入口。
- 新增生产 Graph Node 时，同步在 `integrations/node_lab` 登记 descriptor 和 binding，但禁止修改 `agent.app.lab` 或 Node Lab Service。只消费/返回 JSON-safe State 的 Node 优先用 `DirectNodeExecutor`；只有需要大对象 Artifact 化、依赖注入或输出安全投影时才写专用 Adapter。Graph 节点集合与 Provider descriptor 的一致性属于必跑测试。
- 策略晋升同时暴露无副作用的生产 preview Node；真实 Graph 晋升与 Node Lab preview 共用同一份已验证计划构造，不允许在 Lab 内重写摘要或门禁。
- 两个以上 Node 复用的消息 helper 放入 `app/messages/`；reasoning 日志策略放入 `app/observability/`。
- 不新增把分析、生成、测试和优化混在一起的 `mega_agent_node`。

## 与其他模块的边界

- LLM 抽象从 `app/contracts/` 获取。
- Prompt 主体从 `app/prompts/` 加载。
- 纯输出解析由 `app/parsers/` 完成。
- 图流转和具体 Gateway 装配由 `app/graphs/` 决定。
- M2 角色 Node 仍保持可注入、可单测和不依赖 M1；M3 通过独立 Graph 的预算包装器与确定性 Node 装配它们，不把 Renderer/Oracle/Store 反向塞进角色 Node。
- Backend 负责 persistence 生命周期；Node 只通过 Runtime Store 抽象读取/写入，不持有连接池。
