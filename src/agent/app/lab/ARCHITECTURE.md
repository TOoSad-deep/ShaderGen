# Node Lab 内核架构

`src/agent/app/lab/` 是 Node Lab 的 transport-free Harness 内核。它服务人工调试、Agent/Codex 自动化、模块化测试和 benchmark，但不注册 FastAPI Route，也不直接读取 HTTP DTO。

## 当前能力

共享内核已提供：

- `__init__.py`：保留 Harness 公共名的惰性导出；仅导入 `agent.app.lab.models` 或访问模型契约不会构造 `runner.py` 的 Application 依赖，也不加载 Renderer/Playwright；
- `models.py`：严格、JSON-safe 的请求、响应、节点描述、机器可读输入示例和 Artifact 契约；
- `registry.py`：只校验 Provider 传入的 descriptor 唯一性和 pipeline 一致性，不内置任何生产节点；
- `integration.py`：定义通用 `NodeProvider`、`NodeExecutorBinding`、`NodeExecutionHost` 和适用于 JSON-safe Node 的 `DirectNodeExecutor`；
- `fixtures.py`：带版本和 SHA-256 的成功/Parser 拒绝 Fixture Registry；
- `store.py`：LabRun、不可变步骤快照、DAG 摘要和不透明 Artifact 目录的原子本地存储；
- `runner.py`：可注入执行器的 Python Application API。

确定性能力与 AI-off benchmark 已提供：

- `capabilities.py`：八个可独立调用的确定性 capability 目录及真实输入上限；
- `adapters.py`：只提供复用 `shaderforge.public` 的 normalize、measure、Validator、Renderer、Oracle、Selector，以及生产纯路由函数的独立 capability；它不执行或模拟生产 Node；
- `benchmark.py`：冻结 manifest/hash、真实 capability/node target、scenario/pipeline 的前序 binding 与 `base_step_id` 分支、输入/输出 Artifact 自包含证据、cold/warm Renderer、不可覆盖的中断恢复、JSON/Markdown report 和 fingerprint-aware comparison；
- `suites.py`：只解析三个仓库内固定 AI-off suite id，不接受客户端 manifest 路径；
- `runner.py`：在同一 Application API 上同时执行 capability、节点步骤和 suite，并显式管理可复用 Renderer session。

PNG-to-Shader V1 的 20 个 descriptor、执行模式和具体 Adapter 位于生产侧 `agent.app.nodes.png_to_shader_v1.integrations.node_lab` Provider。Harness 创建时只读取 Provider 的 descriptor，并自动安装 `(node_id, execution_mode)` binding；`pipeline_id` 由 Provider 决定并与 LabRun 绑定。普通 JSON-safe Node 可直接使用 `DirectNodeExecutor`，有 Artifact、Renderer、Memory 或模型依赖的 Node 在生产 Provider 内提供专用 Adapter。新增 Node 不得修改 `agent.app.lab` 或 Node Lab Service；只在所属功能命名空间的 Provider 登记 descriptor/binding，而 Graph 一致性测试会防止漏登记。

PNG-to-Shader Provider 内的 `DeterministicNodeExecutor` 仍只做 JSON-safe State/不透明 Artifact 映射，然后直接调用与 Graph 相同的 Node factory/routing；初始化、测量、候选物化、render/evaluate、selection、best 重载、Review 持久化、finalize 和策略晋升预览没有 Lab 平行实现。`prepare_measurement_seed` 的 Author/GLSL/provenance 仍只写私有 Artifact，独立 root、origin、generator version 和 hash 绑定由生产 `materialize_candidate` 校验。

普通 Renderer capability 与单步生产 `render_and_evaluate` 每次创建并关闭独立浏览器生命周期；单步 Node 只改变依赖生命周期，不复制渲染或评分语义。独立 `renderer_warm` suite 在一次 suite 内复用 capability Renderer，并把 warmup 与 measured attempt 分开记录。`agent.app.benchmarks.model_roles` 的独立模型 runner 默认以 fixture 离线执行五个角色，real 模式要求三重门禁和 semantic/repair/token/wall/cost 硬预算；provider 输出 token cap 在调用前下推，报告按角色分离 Parser/Schema/binding/timeout、latency、用量和模型身份。逐节点 CLI、HTTP/Swagger 与 `/lab` 页面只消费同一 Application API/descriptor；仍不允许 `project_commit` 或真实 Memory 写入。

## 依赖方向

```text
agent.app.services.node_lab
  -> agent.app.lab.runner + models/store/benchmark
  -> agent.app.nodes.png_to_shader_v1.integrations.node_lab（仅公共 NodeProvider）

agent.app.lab.runner
  -> NodeProvider / NodeExecutorBinding 通用协议
  -X-> agent.app.nodes / agent.app.graphs / 具体 Gateway

agent.app.nodes.png_to_shader_v1.integrations.node_lab
  -> production node factories + routing + prompts + parsers
  -> Lab ArtifactStore facade（逻辑 ref 映射为不透明 artifact id）
  -> route_deciders() -> agent.app.lab.runner -> agent.app.lab.adapters

agent.app.lab.adapters
  -> shaderforge.public
  -X-> agent.app.graphs / production routing（只接收 Provider 注入的 RouteDecider）

HTTP / CLI transport
  -> agent.app.services.node_lab
```

`lab/` 不依赖 FastAPI、Backend、LangGraph 编译图、具体 LLM Gateway、`agent.app.nodes.*` 或 `agent.app.graphs.*`。这是模块级依赖边界：`models.py` 不依赖 Runner/Adapter/Renderer，只有显式访问 `NodeLabApplication` 等应用层导出时才惰性加载对应实现。独立 routing capability 也通过 Provider 注入纯函数，不再由 `lab/adapters.py` 导入生产 routing。客户端只能选择 Provider 已描述的 node id，禁止按字符串反射 import。

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
- source fingerprint 同时覆盖 deterministic service、production nodes、Prompt 和 Parser；node profile 必须调用 `execute_step()`，不能用 capability 标签冒充 production node。
- scenario binding 只能引用当前步骤之前的响应；warm suite 必须独立、至少一次 warmup，cold/warm 不能混报。
- 取消或键盘中断先写 interruption JSON；恢复只补缺失 execution，历史中断继续计入失败分母，不覆盖现场。
- 模型恢复从既有 attempt 重新累计 semantic/repair/token/wall/cost，用量不能因重启归零；每个角色 measured 样本少于 20 时 p95 必须为 `null`。
- exact `(node_id, execution_mode)` Executor 优先于通用模式 Executor；执行前后分别检查 descriptor 的必需输入和输出字段，缺失时返回稳定错误且不伪造完成步骤。
- real 模式在分配 step 之前检查服务端开关、请求开关和 Gateway；`project_commit` 同样在任何 Artifact/Memory 副作用前拒绝。
