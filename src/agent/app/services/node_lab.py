"""Node Lab 共享 Harness 的 Agent 公共 Application API."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

from agent.app.contracts.llm import LLMGateway
from agent.app.nodes.png_to_shader_v1.integrations.node_lab import (
    DEFAULT_MODEL_FIXTURE_PATH,
    MemoryReader,
    ResourceCleaner,
    create_png_to_shader_v1_node_provider,
)
from agent.app.nodes.png_to_shader_v1.integrations.node_lab.capability_executor import (
    CapabilityExecutionRuntime,
    DeterministicCapabilityExecutor,
    RendererFactory,
    ShaderRenderer,
    default_renderer_factory,
)
from agent.app.nodes.png_to_shader_v1.integrations.node_lab.capability_registry import (
    build_png_to_shader_v1_capability_registry,
)
from agent.app.nodes.png_to_shader_v1.integrations.node_lab.fixtures import (
    build_png_to_shader_v1_fixture_registry,
)
from agent.app.nodes.png_to_shader_v1.integrations.node_lab.suites import (
    build_png_to_shader_v1_suite_registry,
)
from nodelab.capabilities import CapabilityRegistry
from nodelab.fixtures import FixtureRegistry
from nodelab.integration import (
    AsyncResource,
    BenchmarkResourceFactory,
    CapabilityExecutorFactory,
    CapabilityRuntimeFactory,
    NodeExecutionHost,
    NodeExecutor,
    NodeProvider,
    RouteCapabilityProvider,
    RouteDecider,
)
from nodelab.models import (
    ArtifactDescriptor,
    CapabilityDescriptor,
    CapabilityExecutionResponse,
    LabRunRecord,
    NodeDescriptor,
    StepExecutionResponse,
    StepSummary,
)
from nodelab.models import (
    CapabilityExecutionRequest as CapabilityExecutionRequest,
)
from nodelab.models import EffectMode as EffectMode
from nodelab.models import (
    ExecutionMode as ExecutionMode,
)
from nodelab.models import (
    LabRunCreateRequest as LabRunCreateRequest,
)
from nodelab.models import NodeLabError as NodeLabError
from nodelab.models import (
    StepExecutionRequest as StepExecutionRequest,
)
from nodelab.registry import NodeRegistry
from nodelab.runner import NodeLabApplication as NodeLabApplication
from nodelab.store import NodeLabStore
from nodelab.suites import SuiteRegistry

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_NODE_LAB_ROOT = ROOT / "output/node-lab"
_default_application: NodeLabApplication | None = None


def create_node_lab_application(
    *,
    root: str | Path = DEFAULT_NODE_LAB_ROOT,
    node_provider: NodeProvider | None = None,
    registry: NodeRegistry | None = None,
    fixtures: FixtureRegistry | None = None,
    capability_registry: CapabilityRegistry | None = None,
    capability_executor_factory: CapabilityExecutorFactory | None = None,
    suite_registry: SuiteRegistry | None = None,
    benchmark_resource_factory: BenchmarkResourceFactory | None = None,
    capability_runtime_factory: CapabilityRuntimeFactory | None = None,
    executors: Mapping[ExecutionMode, NodeExecutor] | None = None,
    renderer_factory: RendererFactory = default_renderer_factory,
    memory_reader: MemoryReader | None = None,
    resource_cleaner: ResourceCleaner | None = None,
    model_gateway: LLMGateway | None = None,
    real_model_enabled: bool = False,
    model_fixture_path: str | Path = DEFAULT_MODEL_FIXTURE_PATH,
) -> NodeLabApplication:
    """创建可供 Backend、CLI 或测试持有生命周期的 Node Lab Application."""
    use_default_v1 = node_provider is None and registry is None
    if use_default_v1:
        node_provider = create_png_to_shader_v1_node_provider(
            renderer_factory=renderer_factory,
            memory_reader=memory_reader,
            resource_cleaner=resource_cleaner,
            model_gateway=model_gateway,
            real_model_enabled=real_model_enabled,
            model_fixture_path=model_fixture_path,
        )
        capability_registry = (
            capability_registry or build_png_to_shader_v1_capability_registry()
        )
        suite_registry = suite_registry or build_png_to_shader_v1_suite_registry()
        fixtures = fixtures or build_png_to_shader_v1_fixture_registry()

    route_deciders: Mapping[str, RouteDecider] = (
        node_provider.route_deciders()
        if isinstance(node_provider, RouteCapabilityProvider)
        else {}
    )

    def v1_capability_executor_factory(
        host: NodeExecutionHost,
    ) -> DeterministicCapabilityExecutor:
        return DeterministicCapabilityExecutor(
            host,
            renderer_factory=renderer_factory,
            route_deciders=route_deciders,
        )

    def v1_capability_runtime_factory(
        resource: AsyncResource,
        close_resource: bool,
        resource_usage_count: int,
    ) -> CapabilityExecutionRuntime:
        renderer = cast(ShaderRenderer, resource)
        return CapabilityExecutionRuntime(
            renderer=renderer,
            close_renderer=close_resource,
            browser_launch_count=resource_usage_count,
        )

    if use_default_v1:
        capability_executor_factory = (
            capability_executor_factory or v1_capability_executor_factory
        )
        benchmark_resource_factory = benchmark_resource_factory or renderer_factory
        capability_runtime_factory = (
            capability_runtime_factory or v1_capability_runtime_factory
        )

    application = NodeLabApplication(
        store=NodeLabStore(root),
        node_provider=node_provider,
        registry=registry,
        fixtures=fixtures,
        capability_registry=capability_registry,
        capability_executor_factory=capability_executor_factory,
        suite_registry=suite_registry,
        executors=executors,
        benchmark_resource_factory=benchmark_resource_factory,
        capability_runtime_factory=capability_runtime_factory,
    )
    return application


def create_default_model_node_lab_application(
    *,
    root: str | Path = DEFAULT_NODE_LAB_ROOT,
    real_model_enabled: bool = False,
) -> NodeLabApplication:
    """由 Agent 组合根装配默认模型 Gateway，避免 Backend 依赖 LLM 内部实现."""
    model_gateway: LLMGateway | None = None
    if real_model_enabled:
        from agent.app.llms.gateway import LangChainLLMGateway

        model_gateway = LangChainLLMGateway()
    return create_node_lab_application(
        root=root,
        model_gateway=model_gateway,
        real_model_enabled=real_model_enabled,
    )


def _application(application: NodeLabApplication | None) -> NodeLabApplication:
    """惰性创建本地默认实例，import 本模块不会写 output 目录."""
    global _default_application
    if application is not None:
        return application
    if _default_application is None:
        _default_application = create_node_lab_application()
    return _default_application


def describe_nodes(
    node_id: str | None = None,
    *,
    application: NodeLabApplication | None = None,
) -> tuple[NodeDescriptor, ...]:
    """列出 Node Lab allowlist 目录或单个 descriptor."""
    return _application(application).describe_nodes(node_id)


def describe_capabilities(
    capability_id: str | None = None,
    *,
    application: NodeLabApplication | None = None,
) -> tuple[CapabilityDescriptor, ...]:
    """列出确定性领域能力或单个 descriptor."""
    return _application(application).describe_capabilities(capability_id)


def create_lab_run(
    request: LabRunCreateRequest,
    *,
    application: NodeLabApplication | None = None,
) -> LabRunRecord:
    """创建独立 LabRun 和 root snapshot."""
    return _application(application).create_run(request)


async def execute_step(
    request: StepExecutionRequest,
    *,
    application: NodeLabApplication | None = None,
) -> StepExecutionResponse:
    """执行一次 allowlist 步骤并返回统一响应."""
    return await _application(application).execute_step(request)


async def execute_capability(
    request: CapabilityExecutionRequest,
    *,
    application: NodeLabApplication | None = None,
) -> CapabilityExecutionResponse:
    """执行一个确定性领域能力."""
    return await _application(application).execute_capability(request)


def validate_suite(
    manifest_path: str | Path,
    *,
    application: NodeLabApplication | None = None,
) -> dict[str, object]:
    """校验 Node Lab benchmark manifest 和冻结输入."""
    return _application(application).validate_suite(manifest_path)


async def run_suite(
    manifest_path: str | Path,
    *,
    output_root: str | Path,
    suite_run_id: str | None = None,
    application: NodeLabApplication | None = None,
) -> dict[str, object]:
    """运行 Node Lab AI-off benchmark suite."""
    return await _application(application).run_suite(
        manifest_path,
        output_root=output_root,
        suite_run_id=suite_run_id,
    )


def describe_suites(
    *,
    application: NodeLabApplication | None = None,
) -> tuple[str, ...]:
    """列出 HTTP/CLI 可安全选择的内置 AI-off suite."""
    return _application(application).describe_suites()


def validate_registered_suite(
    suite_id: str,
    *,
    application: NodeLabApplication | None = None,
) -> dict[str, object]:
    """校验一个 allowlist suite，不接收客户端路径."""
    app = _application(application)
    return app.validate_suite(app.resolve_suite(suite_id))


async def run_registered_suite(
    suite_id: str,
    *,
    output_root: str | Path,
    suite_run_id: str | None = None,
    application: NodeLabApplication | None = None,
) -> dict[str, object]:
    """运行一个 allowlist AI-off suite，HTTP 不具备 real 模式."""
    app = _application(application)
    return await app.run_suite(
        app.resolve_suite(suite_id),
        output_root=output_root,
        suite_run_id=suite_run_id,
    )


def get_step(
    lab_run_id: str,
    step_id: str,
    *,
    application: NodeLabApplication | None = None,
) -> StepExecutionResponse:
    """读取已提交步骤响应."""
    return _application(application).get_step(lab_run_id, step_id)


def list_steps(
    lab_run_id: str,
    *,
    application: NodeLabApplication | None = None,
) -> tuple[StepSummary, ...]:
    """按创建顺序列出足以重建 DAG 的步骤摘要."""
    return _application(application).list_step_summaries(lab_run_id)


def list_artifacts(
    lab_run_id: str,
    *,
    application: NodeLabApplication | None = None,
) -> tuple[ArtifactDescriptor, ...]:
    """列出同一 LabRun 的私有 Artifact descriptor."""
    return _application(application).list_artifacts(lab_run_id)


def upload_artifact(
    *,
    lab_run_id: str,
    kind: str,
    content_type: str,
    data: bytes,
    application: NodeLabApplication | None = None,
) -> ArtifactDescriptor:
    """保存私有 Lab Artifact 并返回不透明 descriptor."""
    return _application(application).upload_artifact(
        lab_run_id=lab_run_id,
        kind=kind,
        content_type=content_type,
        data=data,
    )


def read_artifact(
    lab_run_id: str,
    artifact_id: str,
    *,
    application: NodeLabApplication | None = None,
) -> tuple[ArtifactDescriptor, bytes]:
    """读取同一 LabRun 的私有 Artifact."""
    return _application(application).read_artifact(lab_run_id, artifact_id)
