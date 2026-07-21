# Node Lab 内核架构

`src/nodelab/` 是与具体 Agent、Graph 和 Shader 领域解耦的进程内 Python Harness 内核。它服务人工调试、Agent/Codex 自动化、模块化测试和 benchmark，但不注册 FastAPI Route，也不直接读取 HTTP DTO。空 Application 不默认注入任何生产 Node、Fixture、capability 或 suite。

## 当前能力

共享内核已提供：

- `__init__.py`：保留 Harness 公共名的惰性导出；仅导入 `nodelab.models` 或访问模型契约不会构造 `runner.py` 的 Application 依赖，也不加载 Renderer/Playwright；
- `models.py`：严格、JSON-safe 的请求、响应、节点描述、机器可读输入示例和 Artifact 契约；descriptor 必须显式声明 `pipeline_id`，不再默认绑定 V1；
- `registry.py`：只校验 Provider 传入的 descriptor 唯一性和 pipeline 一致性，不内置任何生产节点；
- `integration.py`：定义通用 `NodeProvider`、`NodeExecutorBinding`、`NodeExecutionHost`、`CapabilityExecutor`、warm benchmark 资源协议和适用于 JSON-safe Node 的 `DirectNodeExecutor`；
- `fixtures.py`、`capabilities.py`、`suites.py`：只提供空安全的通用 Registry，不登记 V1 内容；
- `store.py`：LabRun、不可变步骤快照、DAG 摘要和不透明 Artifact 目录的原子本地存储；
- `runner.py`：按单一 `pipeline_id` 装配 Node/Capability/Fixture/Suite 的 Python Application API；跨 Pipeline 读取 LabRun 或运行 manifest 会 fail closed。

确定性能力与 AI-off benchmark 已提供：

- `benchmark.py`：冻结 manifest/hash、真实 capability/node target、scenario/pipeline 的前序 binding 与 `base_step_id` 分支、输入/输出 Artifact 自包含证据、cold/warm Renderer、不可覆盖的中断恢复、JSON/Markdown report 和 fingerprint-aware comparison；
- `runner.py`：在同一 Application API 上执行 capability、节点步骤和 suite，并通过 Provider 注入的通用异步资源协议管理 warm session；内核不认识 Renderer 类型。

PNG-to-Shader V1 的 20 个 descriptor、八个 capability、Fixture、三个 AI-off suite、执行模式和具体 Executor 全部位于生产侧 `agent.app.nodes.png_to_shader_v1.integrations.node_lab`。其中 `capability_executor.py` 才允许依赖 `shaderforge.public` 和 Renderer；Harness 内核不再导入它。`agent.app.services.node_lab` 作为兼容组合根默认装配 V1，但显式传入其他 `NodeProvider` 时不会继承任何 V1 capability、Fixture 或 suite。

PNG-to-Shader Provider 内的 `DeterministicNodeExecutor` 仍只做 JSON-safe State/不透明 Artifact 映射，然后直接调用与 Graph 相同的 Node factory/routing；初始化、测量、候选物化、render/evaluate、selection、best 重载、Review 持久化、finalize 和策略晋升预览没有 Lab 平行实现。`prepare_measurement_seed` 的 Author/GLSL/provenance 仍只写私有 Artifact，独立 root、origin、generator version 和 hash 绑定由生产 `materialize_candidate` 校验。

普通 Renderer capability 与单步生产 `render_and_evaluate` 每次创建并关闭独立浏览器生命周期；单步 Node 只改变依赖生命周期，不复制渲染或评分语义。独立 `renderer_warm` suite 在一次 suite 内复用 capability Renderer，并把 warmup 与 measured attempt 分开记录。`agent.app.benchmarks.model_roles` 的独立模型 runner 默认以 fixture 离线执行五个角色，real 模式要求三重门禁和 semantic/repair/token/wall/cost 硬预算；provider 输出 token cap 在调用前下推，报告按角色分离 Parser/Schema/binding/timeout、latency、用量和模型身份。逐节点 CLI、HTTP/Swagger 与 `/lab` 页面只消费同一 Application API/descriptor；仍不允许 `project_commit` 或真实 Memory 写入。

## 依赖方向

```text
agent.app.services.node_lab
  -> nodelab.runner + models/store/benchmark（通用 Harness）
  -> 默认兼容装配 agent.app.nodes.png_to_shader_v1.integrations.node_lab

nodelab.runner
  -> NodeProvider / NodeExecutorBinding 通用协议
  -X-> agent.app.nodes / agent.app.graphs / 具体 Gateway

agent.app.nodes.png_to_shader_v1.integrations.node_lab
  -> production node factories + routing + prompts + parsers
  -> V1 capability/fixture/suite registry + ShaderForge capability executor
  -> Lab ArtifactStore facade（逻辑 ref 映射为不透明 artifact id）
  -> route_deciders() -> V1 capability executor

HTTP / CLI transport
  -> agent.app.services.node_lab
```

`nodelab` 不依赖 FastAPI、Backend、LangGraph 编译图、具体 LLM Gateway、`agent.app.nodes.*`、`agent.app.graphs.*` 或 ShaderForge 算法/Renderer。它作为当前 `shadergen` distribution 中的独立 typed Python 包发布，而不是独立进程或远程服务；`agent.app.services.node_lab` 仍是仓库默认组合根，Backend 只是可选 transport。`NodeLabStore` 和 benchmark 只复用路径安全、原子写入的 run 级存储工具。客户端只能选择当前 Provider/Registry 描述的 node、capability 和 suite id，禁止按字符串反射 import 或提交 manifest 路径。

## 安全与证据规则

- Node id、Fixture id、LabRun id、Step id 和 Artifact id 都经过受限标识符校验。
- State、请求、响应和 Fixture 只接受有限深度的 JSON-safe 数据；图片、GLSL 等大对象后续必须转为 Artifact。
- 每步先写 request/response/state 文件，最后原子更新步骤索引；索引未提交的目录不可读取为有效步骤。
- `base_step_id` 只允许引用同一 LabRun 已提交步骤，分支不覆盖父快照。
- Artifact 通过不透明 id 查询，不接受客户端路径，也不能跨 LabRun 读取。
- Fixture、请求和执行结果共同形成 `execution_fingerprint`，供后续 benchmark 证据复用。
- capability 拒绝 descriptor 未声明字段，并把领域输入错误归一化为 `input_contract_invalid`；Renderer 尺寸不超过 WebGL1 长边 1024，Validator 字符上限不超过 30000。
- 原始 compiler/console 文本只写私有 diagnostics Artifact；公共响应只给安全摘要和 hash 引用。
- benchmark 在首个 case 前冻结 manifest、Lab/生产路由/ShaderForge 相关源码、依赖版本和环境 fingerprint；失败 attempt 保留在分母，配置漂移禁止原地恢复，复制后的 Artifact 重新校验 SHA-256。
- 新 benchmark manifest 必须声明所属 `pipeline_id`；loader 仍可只读旧的无该字段 v1 manifest，但 Application 会拒绝显式不匹配的 Pipeline。
- source fingerprint 同时覆盖 deterministic service、production nodes、Prompt 和 Parser；node profile 必须调用 `execute_step()`，不能用 capability 标签冒充 production node。
- scenario binding 只能引用当前步骤之前的响应；warm suite 必须独立、至少一次 warmup，cold/warm 不能混报。
- 取消或键盘中断先写 interruption JSON；恢复只补缺失 execution，历史中断继续计入失败分母，不覆盖现场。
- 模型恢复从既有 attempt 重新累计 semantic/repair/token/wall/cost，用量不能因重启归零；每个角色 measured 样本少于 20 时 p95 必须为 `null`。
- exact `(node_id, execution_mode)` Executor 优先于通用模式 Executor；执行前后分别检查 descriptor 的必需输入和输出字段，缺失时返回稳定错误且不伪造完成步骤。
- real 模式在分配 step 之前检查服务端开关、请求开关和 Gateway；`project_commit` 同样在任何 Artifact/Memory 副作用前拒绝。
