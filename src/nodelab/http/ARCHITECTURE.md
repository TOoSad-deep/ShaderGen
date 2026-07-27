# Node Lab 独立服务架构

`src/nodelab/http/` 是 `nodelab` Harness 的独立 FastAPI transport 和进程组合根。它可以不启动产品 Backend、LangGraph Agent、数据库或 ShaderForge 而单独运行；产品 Backend 不注册 `/api/lab/v1/*`，默认端口分别为 `8088` 和 `8090`。

## 组件与依赖方向

```text
可信启动配置 / Application factory
                 |
                 v
nodelab.http.main -> routes -> service -> nodelab.runner
          |              |
          +-> schemas    +-> LabRun / Step / Artifact / benchmark

nodelab.http -X-> agent / backend / shaderforge
backend      -X-> nodelab.http
```

- `settings.py`：一次性读取 `NODELAB_*` 环境变量，冻结数据目录、Pipeline、CORS、真实模型门禁和 factory。
- `factory.py`：默认创建没有 Node、capability、Fixture 或 suite 的空安全 Application；只有进程操作者可通过 `NODELAB_APPLICATION_FACTORY=module:callable` 装配外部 Pipeline。
- `main.py`：创建独立 FastAPI App、CORS 和稳定请求校验错误；不连接产品数据库或 Agent 生命周期。
- `routes/`：以稳定 `router` 聚合入口导出 `/api/lab/v1/*`，内部按共享错误/服务依赖、健康检查、目录、batch、run/step 执行和 Artifact 资源分层；不接受客户端提供的 import 路径、manifest 路径或文件系统路径。
- `schemas/`：以稳定聚合入口导出 HTTP 契约，内部按公共类型、执行资源、batch/report 和错误响应分层。
- `service.py`：把 HTTP DTO 转为通用 `NodeLabApplication` 调用，管理同进程 batch 锁和报告读取。
- `cli.py`：提供 `nodelab-service` 命令，默认监听 `127.0.0.1:8090`。

独立启动不要求先运行 Backend：

```bash
make dev-node-lab
# 或安装 wheel 后直接运行
nodelab-service --host 127.0.0.1 --port 8090
```

## Application factory 契约

Factory 是部署配置，不是远程代码上传接口。目标 callable 接收冻结的 `NodeLabServiceSettings`，返回 `NodeLabApplication`：

```python
def create_application(settings: NodeLabServiceSettings) -> NodeLabApplication:
    provider = NodeProviderBuilder("my_pipeline").add_node(...).build()
    return NodeLabApplication.at_root(settings.root, node_provider=provider)
```

Factory 模块及其 Provider/Node 必须安装在 Node Lab Service 进程的 Python 环境中。当前边界解决独立启动和部署，不提供跨进程任意 Python callable 反射执行；若未来 Node 必须留在另一个进程或语言中，应新增经过鉴权、超时、幂等和 Artifact 约束的 Remote Executor 协议，而不是接受客户端 import 字符串。

## 安全与运行规则

- 无 factory 时服务仍可健康检查、创建 LabRun、上传 Artifact，但节点目录为空；不得静默装配旧 Agent。
- `NODELAB_APPLICATION_FACTORY` 只在进程启动时读取并要求 `module:callable` 受限格式，HTTP API 不暴露修改入口。
- `project_commit`、真实模型、副作用和资源生命周期继续由 Application/Provider 门禁；`NODELAB_REAL_MODEL_ENABLED=true` 只表达服务侧允许，不能替代 Provider 自身授权。
- 数据默认写入 `output/node-lab/service`，batch 写入 `output/benchmarks/node-lab-service`；Artifact 仍使用同一 LabRun 内不透明 id 和 8MB 上传上限。
- HTTP batch 仅允许当前 Application `SuiteRegistry` 中的 suite id，不接受 manifest path；同一 `suite_run_id` 在单进程内串行。
- `make dev-node-lab` 只启动本服务；`make dev-backend` 不导入或注册 Node Lab。
