# Node Lab 使用指南

> 文档状态：`current/optional-tooling`。这是按需调试指南，不是产品链路、当前待办或质量门禁；只有具体需求需要逐节点调试时才启动 Node Lab。

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

## 测试当前 scene_mvp 节点

仓库已提供当前 `png_to_shader_min` 12 节点的显式 Provider/Executor factory。先停止正在运行的空服务，再执行：

```bash
NODELAB_APPLICATION_FACTORY=agent.app.services.node_lab:create_application \
make dev-node-lab
make dev-frontend
```

健康检查应返回 `pipeline_id=scene_mvp`、`node_count=12`：

```bash
curl -s http://127.0.0.1:8090/api/lab/v1/health
curl -s http://127.0.0.1:8090/api/lab/v1/nodes
```

在 `http://127.0.0.1:5173/lab` 中按以下顺序操作：

1. 新建 LabRun，`project_id` 可使用 `node-lab-local`。
2. 在 Artifact 面板上传参考 PNG，kind 使用 `reference_png`。
3. 选择 `initialize_run`，Node Inspector 会依据示例中的 `artifact_inputs` 映射自动把 `source_artifact_id` 填充为上传的 `reference_png` Artifact ID；若同 kind 存在多个 Artifact，则从下拉选择器手动选取。默认示例使用 `fast`、`llm_budget=0`、`refine_budget=0`。
4. 执行后续节点时，Node Inspector 会根据示例的 `base_step_node_id` 自动把 `base_step_id` 选为对应父节点的最新成功 Step；`perceive_target` / `author_initial` / `author_refine` 的 `source_artifact_id`、`render_and_evaluate` / `optimize_base` / `optimize_feature` 的 `target_rgb_artifact_id` 也会按当前 LabRun 的 Artifact 自动填充或下拉选择。没有匹配父步骤时保持 Root State。
5. 依次检查 Output、State Diff、DAG、Artifact 和 `next_action`。路由节点只给出建议动作，不会替用户自动执行整个 Graph。

`deterministic` 模式下，`author_initial` 强制采用感知 fallback，`author_refine` 只保留 `current_best`，不会调用模型。真实模型只对这两个模型节点开放，并且必须同时满足：

```bash
NODELAB_APPLICATION_FACTORY=agent.app.services.node_lab:create_application \
NODELAB_REAL_MODEL_ENABLED=true \
make dev-node-lab
```

请求侧还必须选择 `real`、打开 `allow_model_call`，并在 State 中保留正的 `llm_budget`；Gateway 密钥仍只从服务端 `.env` 读取。

Provider 不把图片、目标 RGB、Render 或生产领域对象直接写入 Lab State：参考图使用不透明 Artifact ID，目标 RGB 使用 NPY Artifact，`current_best` 使用带文档/GLSL 指纹和 Render Artifact ID 的可复验快照。执行时才恢复生产对象并直接调用当前生产 Node。旧 V1 Adapter、Fixture、manifest 和 benchmark 入口没有恢复。

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
