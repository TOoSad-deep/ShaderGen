# Node Lab 内核架构

`src/nodelab/` 是与具体 Agent、Graph、HTTP 和 Shader 领域解耦的 Python Harness。空 Application 不注入任何生产 Node、Fixture、capability 或 suite；独立 HTTP transport 位于 `src/nodelab_service/`。

## 组件

- `models.py`：JSON-safe 请求、响应、descriptor、Artifact 与错误契约。
- `registry.py`、`provider.py`：节点目录、`NodeProviderBuilder` 与绑定。
- `integration.py`：Node/Capability Executor、资源生命周期和 State reducer 协议。
- `fixtures.py`、`capabilities.py`、`suites.py`：空安全通用 Registry。
- `file_store.py`、`store.py`：路径安全原子写入、不可变步骤、DAG 与 Artifact。
- `runner.py`：单一 `pipeline_id` 的 Application API 和 JSON Schema 校验。
- `benchmark.py`：冻结 manifest/fingerprint、attempt、中断恢复与报告。

## 依赖方向

```text
Pipeline factory / Provider / Executor
                 |
                 v
nodelab.runner -> models / registry / store / benchmark
       |
       -X-> agent / backend / shaderforge / FastAPI

nodelab_service -> nodelab.runner
```

`nodelab` 不依赖 FastAPI、Backend、LangGraph 编译图、具体 LLM Gateway、Agent Node 或 ShaderForge。领域 Artifact hydration、模型门禁、Renderer、数据库与 Memory 生命周期由 Pipeline Executor 注入。

## 安全与证据

- 标识符使用受限格式；State、请求、响应和 Fixture 只接受有界 JSON-safe 数据。
- Node/capability 输入输出执行完整 JSON Schema Draft 2020-12 校验。
- `base_step_id` 只能引用同一 LabRun 已提交步骤，分支不覆盖父快照。
- Artifact 使用不透明 ID，不接受客户端路径，也不能跨 LabRun 读取。
- `project_commit` 和未授权 real 模式在产生副作用前拒绝。
- manifest、Provider 源码、依赖版本和环境形成 fingerprint；失败与中断保留在分母。
- 历史 PNG-to-Shader V1 插件、manifest 与 benchmark 入口不属于当前内核，也不会由本次通用包恢复。
