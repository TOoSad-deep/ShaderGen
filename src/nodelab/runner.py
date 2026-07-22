"""Node Lab 的可注入 Python Application API."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError  # type: ignore[import-untyped]

from nodelab.capabilities import CapabilityRegistry
from nodelab.fixtures import FixtureRegistry
from nodelab.integration import (
    AsyncResource,
    BenchmarkResourceFactory,
    CapabilityExecutor,
    CapabilityExecutorFactory,
    CapabilityRuntimeFactory,
    NodeExecutor,
    NodeProvider,
    PreflightNodeExecutor,
    ShallowStateReducer,
    StateReducer,
    discover_implementation_source_paths,
)
from nodelab.models import (
    DEFAULT_NODE_LAB_PIPELINE_ID,
    ArtifactDescriptor,
    CapabilityDescriptor,
    CapabilityExecutionRequest,
    CapabilityExecutionResponse,
    ExecutionMode,
    ExecutionOutcome,
    ExecutionStatus,
    LabRunCreateRequest,
    LabRunRecord,
    NodeDescriptor,
    NodeExecutionResult,
    NodeLabError,
    StateDiff,
    StepExecutionRequest,
    StepExecutionResponse,
    StepSummary,
    ensure_json_object,
)
from nodelab.registry import NodeRegistry
from nodelab.store import NodeLabStore
from nodelab.suites import SuiteRegistry

IdFactory = Callable[[], str]
NowFactory = Callable[[], datetime]
Timer = Callable[[], float]


def _new_id() -> str:
    """生成可用于目录和 API 的 UUID 文本."""
    return str(uuid4())


def _utc_now() -> datetime:
    """返回带时区 UTC 时间."""
    return datetime.now(timezone.utc)


def _isoformat(value: datetime) -> str:
    """把时间规范化为带 Z 的 ISO-8601 文本."""
    if value.tzinfo is None:
        raise ValueError("Node Lab clock 必须返回带时区 datetime。")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _stable_sha256(value: object) -> str:
    """计算 JSON-safe 对象的稳定 SHA-256."""
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


class FixtureNodeExecutor:
    """重放版本化 Fixture，不调用 Graph、Renderer 或模型."""

    def __init__(self, fixtures: FixtureRegistry) -> None:
        """绑定显式 Fixture Registry."""
        self._fixtures = fixtures

    def _fixture_id(
        self,
        descriptor: NodeDescriptor,
        request: StepExecutionRequest,
    ) -> str:
        """解析显式或节点默认 Fixture id."""
        if request.fixture_id:
            return request.fixture_id
        if descriptor.default_fixture_ids:
            return descriptor.default_fixture_ids[0]
        raise NodeLabError(
            "fixture_not_found",
            "该节点尚未登记默认 Fixture，请显式提供 fixture_id。",
            stage="fixture_resolution",
            lab_run_id=request.lab_run_id,
            node_id=request.node_id,
        )

    def resolve_inputs(
        self,
        descriptor: NodeDescriptor,
        request: StepExecutionRequest,
    ) -> dict[str, object]:
        """把 Fixture 输入放在显式覆盖之前."""
        fixture = self._fixtures.get(
            self._fixture_id(descriptor, request),
            node_id=descriptor.node_id,
        )
        return dict(fixture.input_state)

    async def execute(
        self,
        descriptor: NodeDescriptor,
        request: StepExecutionRequest,
        state: Mapping[str, object],
    ) -> NodeExecutionResult:
        """返回经过真实响应契约校验的 Fixture 输出."""
        del state
        fixture = self._fixtures.get(
            self._fixture_id(descriptor, request),
            node_id=descriptor.node_id,
        )
        return NodeExecutionResult(
            outcome=fixture.expected_outcome,
            output_patch=fixture.output_patch,
            diagnostics={
                "fixture_id": fixture.fixture_id,
                "fixture_version": fixture.fixture_version,
                "fixture_sha256": fixture.content_sha256,
            },
            provenance={
                "execution_source": "fixture_registry",
                "fixture_sha256": fixture.content_sha256,
            },
            usage={"model_call_count": 0, "browser_launch_count": 0},
            next_action=fixture.next_action,
        )


def _state_diff(before: dict[str, object], after: dict[str, object]) -> StateDiff:
    """计算顶层 State 的新增、变更和删除字段."""
    before_keys = set(before)
    after_keys = set(after)
    added = {key: after[key] for key in sorted(after_keys - before_keys)}
    changed = {
        key: {"before": before[key], "after": after[key]}
        for key in sorted(before_keys & after_keys)
        if before[key] != after[key]
    }
    return StateDiff(
        added=added,
        changed=changed,
        removed=sorted(before_keys - after_keys),
    )


def _missing_required_fields(
    schema: Mapping[str, object],
    value: Mapping[str, object],
) -> list[str]:
    """返回 descriptor 顶层 Schema 中缺失的稳定字段列表."""
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(
        isinstance(field, str) for field in required
    ):
        raise ValueError("Node descriptor required 必须是字符串数组。")
    return sorted(field for field in required if field not in value)


def _validate_json_schema(
    schema: Mapping[str, object],
    value: Mapping[str, object],
    *,
    code: str,
    message: str,
    stage: str,
    lab_run_id: str | None = None,
    node_id: str | None = None,
) -> None:
    """按 Draft 2020-12 完整校验 JSON Schema，并只返回安全错误位置."""
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError("Node descriptor 包含非法 JSON Schema。") from exc
    errors = sorted(
        Draft202012Validator(schema).iter_errors(value),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    )
    if not errors:
        return
    details = [
        {
            "path": "$"
            + "".join(
                f"[{part}]" if isinstance(part, int) else f".{part}"
                for part in error.absolute_path
            ),
            "validator": str(error.validator),
        }
        for error in errors[:20]
    ]
    raise NodeLabError(
        code,
        message,
        stage=stage,
        lab_run_id=lab_run_id,
        node_id=node_id,
        details={"schema_errors": details},
    )


class NodeLabApplication:
    """供人工工具、测试和 benchmark 共用的单一执行真相源."""

    def __init__(
        self,
        *,
        store: NodeLabStore,
        pipeline_id: str | None = None,
        node_provider: NodeProvider | None = None,
        registry: NodeRegistry | None = None,
        fixtures: FixtureRegistry | None = None,
        capability_registry: CapabilityRegistry | None = None,
        capability_executor_factory: CapabilityExecutorFactory | None = None,
        suite_registry: SuiteRegistry | None = None,
        executors: Mapping[ExecutionMode, NodeExecutor] | None = None,
        node_executors: Mapping[tuple[str, ExecutionMode], NodeExecutor] | None = None,
        benchmark_resource_factory: BenchmarkResourceFactory | None = None,
        capability_runtime_factory: CapabilityRuntimeFactory | None = None,
        state_reducer: StateReducer | None = None,
        benchmark_workspace_root: str | Path | None = None,
        benchmark_source_paths: Iterable[str | Path] = (),
        benchmark_dependency_names: Iterable[str] = (
            "jsonschema",
            "pydantic",
            "PyYAML",
        ),
        id_factory: IdFactory = _new_id,
        now: NowFactory = _utc_now,
        timer: Timer = time.perf_counter,
    ) -> None:
        """注入 Store、NodeProvider、执行器、时钟和 id 来源."""
        if node_provider is not None and registry is not None:
            raise ValueError("node_provider 与 registry 不能同时注入。")
        self._store = store
        self._node_provider = node_provider
        self._registry = (
            NodeRegistry(node_provider.describe_nodes())
            if node_provider
            else (registry or NodeRegistry())
        )
        inferred_pipeline_id = (
            node_provider.pipeline_id
            if node_provider is not None
            else self._registry.pipeline_id
        )
        self._pipeline_id = (
            pipeline_id or inferred_pipeline_id or DEFAULT_NODE_LAB_PIPELINE_ID
        )
        if (
            inferred_pipeline_id is not None
            and inferred_pipeline_id != self._pipeline_id
        ):
            raise ValueError("显式 pipeline_id 与 NodeProvider/Registry 不一致。")
        if (
            self._registry.pipeline_id is not None
            and self._registry.pipeline_id != self._pipeline_id
        ):
            raise ValueError("NodeProvider pipeline_id 与 descriptor 不一致。")
        self._fixtures = fixtures or FixtureRegistry()
        self._capability_registry = capability_registry or CapabilityRegistry()
        if (
            self._capability_registry.pipeline_id is not None
            and self._capability_registry.pipeline_id != self._pipeline_id
        ):
            raise ValueError("Capability Registry pipeline_id 与 Application 不一致。")
        if (
            self._capability_registry.describe_capabilities()
            and capability_executor_factory is None
        ):
            raise ValueError("非空 Capability Registry 必须注入 CapabilityExecutor。")
        self._capability_executor: CapabilityExecutor | None = (
            capability_executor_factory(self)
            if capability_executor_factory is not None
            else None
        )
        self._suite_registry = suite_registry or SuiteRegistry()
        self._benchmark_resource_factory = benchmark_resource_factory
        self._capability_runtime_factory = capability_runtime_factory
        self._state_reducer = state_reducer or ShallowStateReducer()
        self._benchmark_workspace_root = Path(
            benchmark_workspace_root or Path.cwd()
        ).resolve()
        self._benchmark_source_paths = tuple(
            Path(path) for path in benchmark_source_paths
        )
        self._benchmark_dependency_names = tuple(benchmark_dependency_names)
        configured: dict[ExecutionMode, NodeExecutor] = {
            "fixture": FixtureNodeExecutor(self._fixtures),
        }
        configured.update(executors or {})
        self._executors = configured
        self._node_executors = dict(node_executors or {})
        self._id_factory = id_factory
        self._now = now
        self._timer = timer
        if node_provider is not None:
            for binding in node_provider.bind(self):
                self.register_node_executor(
                    node_id=binding.node_id,
                    execution_mode=binding.execution_mode,
                    executor=binding.executor,
                )
            expected = {
                (descriptor.node_id, mode)
                for descriptor in self._registry.describe_nodes()
                for mode in descriptor.execution_modes
            }
            covered_by_mode = {
                (descriptor.node_id, mode)
                for descriptor in self._registry.describe_nodes()
                for mode in descriptor.execution_modes
                if mode in self._executors
            }
            missing = sorted(expected - set(self._node_executors) - covered_by_mode)
            if missing:
                raise ValueError(f"NodeProvider 缺少 Executor 绑定：{missing}。")

    def register_node_executor(
        self,
        *,
        node_id: str,
        execution_mode: ExecutionMode,
        executor: NodeExecutor,
    ) -> None:
        """在服务组合阶段登记精确的 ``(node, mode)`` Executor.

        Node Lab 核心继续只认识 ``NodeExecutor`` 协议。生产 Node、Gateway、
        Memory 等依赖由 ``agent.app.services`` 构造后通过这里注入，避免核心包
        反向依赖实现层。重复登记视为组合错误，不能静默覆盖。
        """
        descriptor = self._registry.get(node_id)
        if execution_mode not in descriptor.execution_modes:
            raise ValueError(
                f"节点 {node_id} 未声明执行模式 {execution_mode}，禁止登记 Executor。"
            )
        key = (node_id, execution_mode)
        if key in self._node_executors:
            raise ValueError(
                f"节点 {node_id} 的 {execution_mode} Executor 已登记，禁止覆盖。"
            )
        self._node_executors[key] = executor

    @classmethod
    def at_root(
        cls,
        root: str | Path,
        **kwargs: object,
    ) -> NodeLabApplication:
        """为 CLI、测试或 Backend 生命周期创建独立 Application 实例."""
        return cls(store=NodeLabStore(root), **kwargs)  # type: ignore[arg-type]

    def describe_nodes(self, node_id: str | None = None) -> tuple[NodeDescriptor, ...]:
        """列出全部节点，或返回单个 allowlist descriptor."""
        if node_id is None:
            return self._registry.describe_nodes()
        return (self._registry.get(node_id),)

    @property
    def pipeline_id(self) -> str:
        """返回当前 Application 的稳定 Pipeline 作用域."""
        return self._pipeline_id

    def benchmark_source_paths(self) -> tuple[Path, ...]:
        """返回 Provider 声明的 benchmark 生产源文件."""
        provider_paths = (
            tuple(Path(path) for path in self._node_provider.source_paths())
            if self._node_provider is not None
            else ()
        )
        reducer_paths = tuple(
            Path(path)
            for path in discover_implementation_source_paths(self._state_reducer)
        )
        return (*self._benchmark_source_paths, *provider_paths, *reducer_paths)

    @property
    def benchmark_workspace_root(self) -> Path:
        """返回环境 fingerprint 使用的调用方工作区根."""
        return self._benchmark_workspace_root

    @property
    def benchmark_dependency_names(self) -> tuple[str, ...]:
        """返回调用方声明的环境依赖包名."""
        return self._benchmark_dependency_names

    def describe_capabilities(
        self,
        capability_id: str | None = None,
    ) -> tuple[CapabilityDescriptor, ...]:
        """列出八个确定性能力，或返回单个 descriptor."""
        if capability_id is None:
            return self._capability_registry.describe_capabilities()
        return (self._capability_registry.get(capability_id),)

    def describe_suites(self) -> tuple[str, ...]:
        """列出当前 Pipeline Provider 登记的 benchmark suite."""
        return self._suite_registry.describe()

    def resolve_suite(self, suite_id: str) -> Path:
        """解析当前 Pipeline Provider 登记的 benchmark manifest."""
        return self._suite_registry.resolve(suite_id)

    def create_run(self, request: LabRunCreateRequest) -> LabRunRecord:
        """创建不复用产品 run_id 的独立 LabRun."""
        lab_run_id = self._id_factory()
        root_state = ensure_json_object(request.initial_state)
        record = LabRunRecord(
            pipeline_id=self._pipeline_id,
            lab_run_id=lab_run_id,
            project_id=request.project_id,
            created_at=_isoformat(self._now()),
            root_state_sha256=_stable_sha256(root_state),
        )
        return self._store.create_run(record, root_state)

    def get_run(self, lab_run_id: str) -> LabRunRecord:
        """读取已创建的 LabRun 元数据."""
        record = self._store.load_run(lab_run_id)
        if record.pipeline_id != self._pipeline_id:
            raise NodeLabError(
                "pipeline_scope_mismatch",
                "LabRun 不属于当前 NodeProvider pipeline。",
                stage="pipeline_scope",
                lab_run_id=lab_run_id,
                details={
                    "run_pipeline_id": record.pipeline_id,
                    "provider_pipeline_id": self._pipeline_id,
                },
            )
        return record

    def upload_artifact(
        self,
        *,
        lab_run_id: str,
        kind: str,
        content_type: str,
        data: bytes,
    ) -> ArtifactDescriptor:
        """保存私有 Lab Artifact 并返回不含路径的 descriptor."""
        self.get_run(lab_run_id)
        descriptor = ArtifactDescriptor(
            artifact_id=self._id_factory(),
            lab_run_id=lab_run_id,
            kind=kind,
            content_type=content_type,
            sha256=sha256(data).hexdigest(),
            size_bytes=len(data),
            created_at=_isoformat(self._now()),
        )
        return self._store.put_artifact(descriptor=descriptor, data=data)

    def read_artifact(
        self,
        lab_run_id: str,
        artifact_id: str,
    ) -> tuple[ArtifactDescriptor, bytes]:
        """按同一 LabRun 的不透明 id 读取 Artifact."""
        self.get_run(lab_run_id)
        return self._store.read_artifact(lab_run_id, artifact_id)

    def list_step_ids(self, lab_run_id: str) -> tuple[str, ...]:
        """列出已原子提交的步骤 id."""
        self.get_run(lab_run_id)
        return self._store.list_step_ids(lab_run_id)

    def list_step_summaries(self, lab_run_id: str) -> tuple[StepSummary, ...]:
        """按创建顺序返回足以重建步骤 DAG 的安全摘要."""
        self.get_run(lab_run_id)
        return tuple(
            StepSummary(
                lab_run_id=response.lab_run_id,
                step_id=response.step_id,
                base_step_id=response.base_step_id,
                node_id=response.node_id,
                execution_mode=response.execution_mode,
                execution_status=response.execution_status,
                outcome=response.outcome,
                artifact_count=len(response.artifacts),
                next_action=response.next_action,
                duration_ms=response.duration_ms,
                execution_fingerprint=response.execution_fingerprint,
                created_at=response.created_at,
            )
            for step_id in self._store.list_step_ids(lab_run_id)
            for response in (self._store.load_step_response(lab_run_id, step_id),)
        )

    def list_artifacts(self, lab_run_id: str) -> tuple[ArtifactDescriptor, ...]:
        """列出同一 LabRun 的 Artifact descriptor，不暴露 payload 路径."""
        self.get_run(lab_run_id)
        return self._store.list_artifacts(lab_run_id)

    def get_step(self, lab_run_id: str, step_id: str) -> StepExecutionResponse:
        """读取一个已提交步骤的稳定响应."""
        self.get_run(lab_run_id)
        return self._store.load_step_response(lab_run_id, step_id)

    def _base_state(self, request: StepExecutionRequest) -> dict[str, object]:
        """只从 root 或同一 LabRun 已提交步骤读取父快照."""
        self.get_run(request.lab_run_id)
        if request.base_step_id is None:
            return dict(self._store.load_root_state(request.lab_run_id))
        return dict(
            self._store.load_state_after(request.lab_run_id, request.base_step_id)
        )

    def _executor(
        self,
        descriptor: NodeDescriptor,
        request: StepExecutionRequest,
    ) -> NodeExecutor:
        """校验 descriptor 声明与实际注入执行器一致，缺失时 fail closed."""
        if request.execution_mode not in descriptor.execution_modes:
            raise NodeLabError(
                "unsupported_execution_mode",
                "该节点不支持请求的执行模式。",
                stage="executor_resolution",
                lab_run_id=request.lab_run_id,
                node_id=request.node_id,
                details={"execution_mode": request.execution_mode},
            )
        exact = self._node_executors.get((descriptor.node_id, request.execution_mode))
        if exact is not None:
            return exact
        try:
            return self._executors[request.execution_mode]
        except KeyError as exc:
            raise NodeLabError(
                "executor_not_configured",
                "当前 Application 未配置该执行模式。",
                stage="executor_resolution",
                lab_run_id=request.lab_run_id,
                node_id=request.node_id,
                details={"execution_mode": request.execution_mode},
            ) from exc

    async def execute_step(
        self,
        request: StepExecutionRequest,
    ) -> StepExecutionResponse:
        """从不可变父快照执行一次 allowlist 步骤并提交完整证据."""
        descriptor = self._registry.get(request.node_id)
        if request.effect_mode == "project_commit":
            raise NodeLabError(
                "effect_not_allowed",
                "Node Lab V1 禁止写入任何项目级数据。",
                stage="effect_policy",
                lab_run_id=request.lab_run_id,
                node_id=request.node_id,
            )
        executor = self._executor(descriptor, request)
        if isinstance(executor, PreflightNodeExecutor):
            executor.preflight(descriptor, request)
        base_state = self._base_state(request)
        resolved_inputs = ensure_json_object(
            executor.resolve_inputs(descriptor, request),
            path="$.resolved_inputs",
        )
        state_before: dict[str, object] = {
            **base_state,
            **resolved_inputs,
            **request.inputs,
        }
        ensure_json_object(state_before, path="$.state_before")
        missing_inputs = _missing_required_fields(
            descriptor.input_schema,
            state_before,
        )
        if missing_inputs:
            raise NodeLabError(
                "node_prerequisite_missing",
                "节点缺少 descriptor 声明的前置输入。",
                stage="input_resolution",
                lab_run_id=request.lab_run_id,
                node_id=request.node_id,
                details={"missing_fields": missing_inputs},
            )
        _validate_json_schema(
            descriptor.input_schema,
            state_before,
            code="input_contract_invalid",
            message="节点输入不符合 descriptor JSON Schema。",
            stage="input_validation",
            lab_run_id=request.lab_run_id,
            node_id=request.node_id,
        )
        step_id = self._id_factory()
        started = self._timer()
        output: dict[str, object] = {}
        state_after = dict(state_before)
        try:
            result = await executor.execute(descriptor, request, state_before)
            missing_outputs = _missing_required_fields(
                descriptor.output_schema,
                result.output_patch,
            )
            if missing_outputs:
                raise NodeLabError(
                    "internal_invariant_failed",
                    "节点 Executor 缺少 descriptor 声明的输出。",
                    stage="output_projection",
                    lab_run_id=request.lab_run_id,
                    node_id=request.node_id,
                    details={"missing_fields": missing_outputs},
                )
            _validate_json_schema(
                descriptor.output_schema,
                result.output_patch,
                code="internal_invariant_failed",
                message="节点输出不符合 descriptor JSON Schema。",
                stage="output_validation",
                lab_run_id=request.lab_run_id,
                node_id=request.node_id,
            )
            output = dict(result.output_patch)
            try:
                state_after = ensure_json_object(
                    self._state_reducer.apply(state_before, output),
                    path="$.state_after",
                )
            except Exception as exc:
                raise NodeLabError(
                    "internal_invariant_failed",
                    "State reducer 无法生成合法的 JSON-safe State。",
                    stage="state_reduction",
                    lab_run_id=request.lab_run_id,
                    node_id=request.node_id,
                    details={"error_type": type(exc).__name__},
                ) from exc
        except NodeLabError as exc:
            result = None
            error_detail = exc.to_detail()
        except Exception as exc:  # noqa: BLE001 - 必须保存安全失败证据
            result = None
            error_detail = {
                "code": "internal_invariant_failed",
                "message": "Node Lab Executor 发生内部错误。",
                "stage": "node_execution",
                "retryable": False,
                "error_type": type(exc).__name__,
            }
        duration_ms = max(0.0, (self._timer() - started) * 1000.0)
        created_at = _isoformat(self._now())
        input_summary = {
            "field_names": sorted(state_before),
            "state_sha256": _stable_sha256(state_before),
            "base_step_id": request.base_step_id,
        }

        execution_status: ExecutionStatus
        outcome: ExecutionOutcome
        if result is None:
            output = {}
            state_after = dict(state_before)
            diagnostics = {"error": error_detail}
            provenance = {"execution_source": request.execution_mode}
            usage: dict[str, object] = {}
            artifacts: list[ArtifactDescriptor] = []
            next_action = None
            execution_status = "failed"
            outcome = "failed"
        else:
            output = dict(result.output_patch)
            diagnostics = dict(result.diagnostics)
            provenance = dict(result.provenance)
            usage = dict(result.usage)
            artifacts = list(result.artifacts)
            next_action = result.next_action
            execution_status = "completed"
            outcome = result.outcome

        diff = _state_diff(state_before, state_after)
        fingerprint = _stable_sha256(
            {
                "request": request.to_dict(),
                "input_summary": input_summary,
                "output": output,
                "diagnostics": diagnostics,
                "provenance": provenance,
                "outcome": outcome,
                "state_after_sha256": _stable_sha256(state_after),
            }
        )
        response = StepExecutionResponse(
            pipeline_id=self._pipeline_id,
            lab_run_id=request.lab_run_id,
            step_id=step_id,
            base_step_id=request.base_step_id,
            node_id=request.node_id,
            execution_mode=request.execution_mode,
            execution_status=execution_status,
            outcome=outcome,
            input_summary=input_summary,
            output=output,
            state_diff=diff,
            artifacts=artifacts,
            diagnostics=diagnostics,
            provenance=provenance,
            usage=usage,
            next_action=next_action,
            duration_ms=duration_ms,
            execution_fingerprint=fingerprint,
            created_at=created_at,
        )
        self._store.commit_step(
            request=request,
            response=response,
            state_before=state_before,
            state_after=state_after,
        )
        return response

    async def execute_capability(
        self,
        request: CapabilityExecutionRequest,
    ) -> CapabilityExecutionResponse:
        """执行独立确定性能力并返回可直接进入 benchmark 的响应."""
        return await self._execute_capability(request)

    @asynccontextmanager
    async def benchmark_resource_session(self) -> AsyncIterator[AsyncResource]:
        """为 warm benchmark 持有一个 Pipeline 自定义资源生命周期."""
        if self._benchmark_resource_factory is None:
            raise NodeLabError(
                "benchmark_resource_not_configured",
                "当前 Pipeline 未配置 warm benchmark 资源。",
                stage="benchmark_resource",
            )
        resource = self._benchmark_resource_factory()
        try:
            yield resource
        finally:
            try:
                await resource.close()
            except Exception as exc:  # noqa: BLE001 - 生命周期失败安全归一化
                raise NodeLabError(
                    "benchmark_resource_unavailable",
                    "Node Lab warm benchmark 资源清理失败。",
                    stage="benchmark_resource_cleanup",
                    retryable=True,
                ) from exc

    async def execute_capability_with_resource(
        self,
        request: CapabilityExecutionRequest,
        *,
        resource: AsyncResource,
        resource_usage_count: int,
    ) -> CapabilityExecutionResponse:
        """在显式 suite 级领域资源中执行一次 capability."""
        if self._capability_runtime_factory is None:
            raise NodeLabError(
                "capability_runtime_not_configured",
                "当前 Pipeline 未配置 capability runtime。",
                stage="capability_runtime",
            )
        return await self._execute_capability(
            request,
            runtime=self._capability_runtime_factory(
                resource,
                False,
                resource_usage_count,
            ),
        )

    async def _execute_capability(
        self,
        request: CapabilityExecutionRequest,
        *,
        runtime: object | None = None,
    ) -> CapabilityExecutionResponse:
        """统一构造 cold/warm capability 响应，避免 benchmark 复制语义."""
        descriptor = self._capability_registry.get(request.capability_id)
        self.get_run(request.lab_run_id)
        execution_id = self._id_factory()
        started = self._timer()
        try:
            _validate_json_schema(
                descriptor.input_schema,
                request.inputs,
                code="input_contract_invalid",
                message="Capability 输入不符合 descriptor JSON Schema。",
                stage="capability_input_validation",
                lab_run_id=request.lab_run_id,
            )
            if self._capability_executor is None:
                raise NodeLabError(
                    "capability_not_configured",
                    "当前 Pipeline 未配置 capability executor。",
                    stage="capability_execution",
                    lab_run_id=request.lab_run_id,
                )
            result = await self._capability_executor.execute_capability(
                request,
                descriptor,
                runtime,
            )
            _validate_json_schema(
                descriptor.output_schema,
                result.output_patch,
                code="internal_invariant_failed",
                message="Capability 输出不符合 descriptor JSON Schema。",
                stage="capability_output_validation",
                lab_run_id=request.lab_run_id,
            )
        except NodeLabError as exc:
            result = None
            error_detail = exc.to_detail()
        except Exception as exc:  # noqa: BLE001 - 统一保存安全失败证据
            result = None
            error_detail = {
                "code": "internal_invariant_failed",
                "message": "Node Lab capability 发生内部错误。",
                "stage": "capability_execution",
                "retryable": False,
                "error_type": type(exc).__name__,
            }
        duration_ms = max(0.0, (self._timer() - started) * 1000.0)
        input_summary = {
            "field_names": sorted(request.inputs),
            "inputs_sha256": _stable_sha256(request.inputs),
        }
        execution_status: ExecutionStatus
        outcome: ExecutionOutcome
        if result is None:
            output: dict[str, object] = {}
            artifacts: list[ArtifactDescriptor] = []
            diagnostics = {"error": error_detail}
            provenance = {"execution_source": "pipeline_capability"}
            usage: dict[str, object] = {}
            execution_status = "failed"
            outcome = "failed"
        else:
            output = dict(result.output_patch)
            artifacts = list(result.artifacts)
            diagnostics = dict(result.diagnostics)
            provenance = dict(result.provenance)
            usage = dict(result.usage)
            execution_status = "completed"
            outcome = result.outcome
        fingerprint = _stable_sha256(
            {
                "request": request.to_dict(),
                "input_summary": input_summary,
                "output": output,
                "diagnostics": diagnostics,
                "provenance": provenance,
                "outcome": outcome,
            }
        )
        return CapabilityExecutionResponse(
            pipeline_id=self._pipeline_id,
            lab_run_id=request.lab_run_id,
            capability_execution_id=execution_id,
            capability_id=request.capability_id,
            execution_status=execution_status,
            outcome=outcome,
            input_summary=input_summary,
            output=output,
            artifacts=artifacts,
            diagnostics=diagnostics,
            provenance=provenance,
            usage=usage,
            duration_ms=duration_ms,
            execution_fingerprint=fingerprint,
            created_at=_isoformat(self._now()),
        )

    def validate_suite(self, manifest_path: str | Path) -> dict[str, object]:
        """校验版本化 benchmark manifest、文件 hash 和 capability allowlist."""
        from nodelab.benchmark import load_benchmark_manifest

        capability_ids = {
            descriptor.capability_id
            for descriptor in self._capability_registry.describe_capabilities()
        }
        node_ids = {
            descriptor.node_id for descriptor in self._registry.describe_nodes()
        }
        suite = load_benchmark_manifest(
            manifest_path,
            capability_ids=capability_ids,
            node_ids=node_ids,
        )
        if (
            suite.manifest.pipeline_id is not None
            and suite.manifest.pipeline_id != self._pipeline_id
        ):
            raise ValueError("benchmark manifest pipeline_id 与 Application 不一致。")
        return suite.summary()

    async def run_suite(
        self,
        manifest_path: str | Path,
        *,
        output_root: str | Path,
        suite_run_id: str | None = None,
    ) -> dict[str, object]:
        """运行冻结的 AI-off suite 并生成逐 attempt 证据和报告."""
        from nodelab.benchmark import (
            load_benchmark_manifest,
            run_benchmark_suite,
        )

        capability_ids = {
            descriptor.capability_id
            for descriptor in self._capability_registry.describe_capabilities()
        }
        node_ids = {
            descriptor.node_id for descriptor in self._registry.describe_nodes()
        }
        suite = load_benchmark_manifest(
            manifest_path,
            capability_ids=capability_ids,
            node_ids=node_ids,
        )
        if (
            suite.manifest.pipeline_id is not None
            and suite.manifest.pipeline_id != self._pipeline_id
        ):
            raise ValueError("benchmark manifest pipeline_id 与 Application 不一致。")
        report = await run_benchmark_suite(
            self,
            suite,
            output_root=output_root,
            suite_run_id=suite_run_id,
        )
        return report
