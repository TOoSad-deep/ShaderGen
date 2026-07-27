# Node Lab 使用指南

Node Lab 是 Pipeline 无关的节点实验 Harness 和独立 FastAPI 服务。它提供节点目录、不可变 LabRun/Step、Artifact、Fixture、capability 与 benchmark 契约，但不会自动导入 Agent、Graph 或 ShaderForge。

## 启动

分别启动服务和前端：

```bash
make dev-node-lab
make dev-frontend
```

- 工作台：`http://127.0.0.1:5173/lab`
- Swagger：`http://127.0.0.1:8090/docs`

默认服务使用空安全 Application，因此健康检查可用但节点目录为空。要执行项目节点，必须由受信任的进程配置注入 factory：

```python
from nodelab import NodeLabApplication, NodeProviderBuilder
from nodelab.http import NodeLabServiceSettings


def create_application(settings: NodeLabServiceSettings) -> NodeLabApplication:
    provider = NodeProviderBuilder("my_pipeline").add_node(...).build()
    return NodeLabApplication.at_root(settings.root, node_provider=provider)
```

```bash
NODELAB_APPLICATION_FACTORY=my_project.node_lab:create_application make dev-node-lab
```

Factory 模块及其 Node/Provider 必须安装在服务进程中。客户端不能提交 import path、manifest path 或文件系统路径。

## 环境变量

```text
NODELAB_ROOT=output/node-lab/service
NODELAB_BATCH_ROOT=output/benchmarks/node-lab-service
NODELAB_PIPELINE_ID=node_lab
NODELAB_APPLICATION_FACTORY=
NODELAB_CORS_ORIGINS=http://127.0.0.1:5173,http://localhost:5173
NODELAB_LOG_LEVEL=INFO
NODELAB_REAL_MODEL_ENABLED=false
```

`NODELAB_REAL_MODEL_ENABLED=true` 只表示 transport 允许真实模型请求；Provider 仍必须自行注入 Gateway、预算与权限。`project_commit` 默认拒绝。

## 接入规则

1. 普通 JSON-safe `node(state) -> partial state` 优先使用 `NodeProviderBuilder`。
2. `node(state, context)`、Runnable 或 Command-like 返回值使用对应标准 Executor。
3. Pipeline 自己提供 Fixture、capability、suite、State reducer 与资源生命周期；领域适配不得放入 `src/nodelab/`。
4. Artifact hydration、模型门禁、Renderer、数据库、Memory 生命周期和输出脱敏留在 Pipeline 的薄 Executor。
5. descriptor、Fixture、suite 与 LabRun 必须使用同一 `pipeline_id`；跨 Pipeline 访问会 fail closed。

最小示例：

```python
from pydantic import BaseModel

from nodelab import NodeLabApplication, NodeProviderBuilder


class AddInput(BaseModel):
    left: int
    right: int


class AddOutput(BaseModel):
    total: int


def add_node(state: dict[str, object]) -> dict[str, object]:
    return {"total": int(state["left"]) + int(state["right"])}


provider = (
    NodeProviderBuilder("example_pipeline")
    .add_node(
        add_node,
        input_model=AddInput,
        output_model=AddOutput,
        example_inputs={"left": 1, "right": 2},
    )
    .build()
)
application = NodeLabApplication.at_root(
    "output/node-lab/example",
    node_provider=provider,
)
```

输入和输出按完整 JSON Schema Draft 2020-12 校验。步骤通过 `base_step_id` 从同一 LabRun 的父快照分支，永不覆盖父步骤。

## HTTP 最小流程

```bash
curl -s http://127.0.0.1:8090/api/lab/v1/health
curl -s http://127.0.0.1:8090/api/lab/v1/nodes
curl -s -X POST http://127.0.0.1:8090/api/lab/v1/runs \
  -H 'Content-Type: application/json' \
  -d '{"project_id":"tutorial","initial_state":{}}'
```

服务默认目录为空时，创建 LabRun 和上传 Artifact 仍可用，但执行 Node 会返回稳定错误。配置 factory 后，工作台和 Swagger 都只展示 factory 返回的 allowlist。

## 安全与证据边界

- 产品 Backend 不注册 `/api/lab/v1/*`。
- 数据默认写入 `output/node-lab/service`，batch 输出写入 `output/benchmarks/node-lab-service`。
- Artifact 使用同一 LabRun 内不透明 ID，不能跨 LabRun 读取。
- `project_commit`、真实模型和外部副作用必须在任何写入前通过双重门禁。
- 历史 PNG-to-Shader V1 benchmark 入口保持退役；通用 Node Lab 不恢复旧 V1 Graph、manifest、脚本或证据。
