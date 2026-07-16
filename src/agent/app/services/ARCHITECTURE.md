# Services 架构

`src/agent/app/services/` 是 Backend、CLI、benchmark 和测试调用 Agent 的应用层公共边界。产品请求只使用 PNG-to-Shader V1 service；Node Lab 相关 service 是显式调用的诊断 Harness 边界，不属于产品请求链路。

## 当前服务

- `png_to_shader_v1.py`：暴露服务端自动 render/evaluate/review/refine 用例、V1 Memory 清理和固定 Artifact 白名单读取。
- `node_lab.py`：暴露 Node Lab 的 transport-free Application API，并仅把公共 `NodeProvider` 注入通用 Harness；不再导入具体 Node factory、routing、节点 ID 集合或模式分支。HTTP/CLI/测试必须复用此入口。
- `agent.app.nodes.png_to_shader_v1.integrations.node_lab`：属于 V1 生产 Node 命名空间的对外集成包；维护 20 个 descriptor，分别描述 15 个非模型节点和五个模型节点。Provider 由 `DeterministicNodeExecutor` 为前者提供 deterministic binding，由 `ModelRoleExecutor` 为后者提供 fixture/mock/real binding；它同时声明 routing capability 和 benchmark 源文件。
- `errors.py`：保存公共用例使用的安全 persistence 异常。

五角色 fixture/real benchmark 属于离线 `agent.app.benchmarks.model_roles`，不属于本 Service 边界。

产品 Backend 默认不注册 Node Lab Route；只有在进程启动前显式设置 `SHADERGEN_NODE_LAB_ENABLED=true`（或使用 `make dev-node-lab`）才开放 `/api/lab/v1/*`。该 HTTP 开关只决定诊断 transport 是否注册，不会把 Harness 变成产品 service，也不会自行触发模型调用。

当前公共入口：

- `generate_png_to_shader_v1()`：按质量档位执行 M3 Graph，拒绝没有通过硬门禁的终止结果。
- `create_png_to_shader_v1_service()`：把 Backend checkpointer、Memory `BaseStore` 与 ShaderForge `LocalArtifactStore` 注入独立 V1 Graph。
- `PngToShaderV1Service.invoke()`：与 Graph 共享 run 级 Renderer registry；`finalize` 负责正常关闭，Service `finally` 对 Graph 外异常执行限时、幂等兜底，清理失败只记录安全异常类型且不覆盖业务结果。
- `PngToShaderV1Service.clear_memory()`：删除 V1 checkpoint、旧 Graph 遗留的裸 project thread 和项目 Store Memory；兼容清理不恢复旧 Graph 运行入口。
- `PngToShaderV1Service.read_public_artifact()`：只解析 `final-render`、`metrics`、`manifest`，不接收文件路径。
- `create_node_lab_application()`：为显式诊断用的 Backend、CLI、benchmark 或测试创建独立 Harness 生命周期；默认注入 PNG-to-Shader Provider，也可注入其他实现同一协议的 Provider。
- `create_default_model_node_lab_application()`：只在 Agent 公共组合根按服务端开关装配具体 Gateway，Backend 不依赖 `agent.app.llms`。
- `describe_nodes()`、`describe_capabilities()`、`create_lab_run()`、`execute_step()`、`execute_capability()`：访问带机器可读示例的 20 节点与八个确定性能力 allowlist，并执行 transport-free Lab 步骤。
- `validate_suite()`、`run_suite()`：校验冻结 manifest 并生成逐 attempt 证据和报告，不经过 FastAPI。
- `describe_suites()`、`validate_registered_suite()`、`run_registered_suite()`：只解析三个仓库内固定 AI-off suite id，供 HTTP batch 使用，不接受客户端路径。
- `list_steps()`、`get_step()`：返回完整步骤或可重建 `base_step_id` DAG 的安全摘要。
- `upload_artifact()`、`list_artifacts()`、`read_artifact()`：仅通过同一 LabRun 的不透明 id 保存、列出和读取私有实验产物。

## Service 规则

- Service 接收简单 Python 参数，不暴露 LangChain 消息类型给后端。
- 产品 Service 调用 Graph，不绕过 Graph 直接调用节点或模型。Node Lab 是调试/测试 Harness 的明确例外：其 Executor 把 Lab Artifact/JSON 映射为生产 Node 输入并调用同一 Node factory；适配层只处理传输形状、私有 Artifact 和副作用门禁，不复制状态转换。模型仍只通过 `LLMGateway` 契约注入，且不进入产品请求链路。
- 本文中的 ShaderForge Artifact Store 只指 `LocalArtifactStore` 和 run 级 `RunArtifactStore`；Memory `BaseStore` 与诊断 Harness 的 `NodeLabStore` 分别拥有独立的长期记忆和 LabRun 索引/访问语义，不能统称为同一个 Store。
- Service 把图输出映射为稳定 dataclass。
- V1 成功 dataclass 显式区分已评分 `current_best` 与 `unscored_fallback`；后者仍有 GLSL/render/candidate id，但 `score=None`，且只附加 candidate id 匹配最终结果的 Review。
- Service 可以 re-export 稳定 Parser 函数，但不要 import `nodes/` 中的内部 helper。
- Agent 不直接持有数据库连接池；过程数据通过 service 结果返回给后端统一落库。
- Graph Builder 创建并注入 run 级资源时，负责执行 Graph 的公共 Service 必须共享同一资源 registry，并为越过 Graph 终止路径的未知异常提供幂等清理；不得只依赖某个 finalize Node。
- V1 使用 `png-to-shader-v1:{project_id}` 隔离 checkpoint，同时继续用原 project_id 读取项目 Store Memory，并返回 `durable`、`ephemeral` 或 `degraded` memory status。
- 后端只依赖 service 的公共函数和结果类型。
- Node Lab Route 和 CLI 只能调用 `agent.app.services.node_lab`；不得复制 Registry、Fixture 解析、State diff、fingerprint 或 Artifact 规则。
- Node Lab Service 不得维护 descriptor、`SUPPORTED_NODE_IDS` 或逐节点 dispatch。新 Node 由生产 Provider 声明 descriptor/binding；JSON-safe Node 可用通用 `DirectNodeExecutor`，有 Artifact、Renderer、Memory 或模型依赖的 Node 由 Provider 提供专用 Executor。当前 V1 的 15 个非模型节点统一绑定 `DeterministicNodeExecutor`，五个模型节点统一绑定 `ModelRoleExecutor`，不能把 20 个 descriptor 误写成 20 个独立 Adapter。Provider descriptor 必须与生产 Graph 节点集合一致，且每个声明模式都必须有精确或通用 Executor，否则 Application 构造失败。
