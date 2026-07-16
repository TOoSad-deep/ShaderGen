"""PNG-to-Shader V1 Node 向通用 Node Lab 暴露的公共 Provider."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent.app.contracts.llm import LLMGateway
from agent.app.graphs.png_to_shader_v1_routing import (
    decide_after_render,
    decide_after_selection,
)
from agent.app.lab.adapters import RendererFactory, default_renderer_factory
from agent.app.lab.integration import (
    NodeExecutionHost,
    NodeExecutorBinding,
    RouteDecider,
)
from agent.app.lab.models import ExecutionMode, NodeDescriptor
from agent.app.lab.registry import NodeRegistry

from .deterministic import (
    DeterministicNodeExecutor,
    MemoryReader,
    ResourceCleaner,
)
from .model import (
    DEFAULT_MODEL_FIXTURE_PATH,
    ModelRoleExecutor,
)
from .registry import (
    build_png_to_shader_v1_descriptors,
)

PIPELINE_ID = "png_to_shader_v1"
ROOT = Path(__file__).resolve().parents[7]
MODEL_EXECUTION_MODES: tuple[ExecutionMode, ...] = ("fixture", "mock", "real")


@dataclass(frozen=True)
class PngToShaderV1NodeProvider:
    """20 个生产 Node 的 descriptor 和 Executor 装配入口."""

    renderer_factory: RendererFactory = default_renderer_factory
    memory_reader: MemoryReader | None = None
    resource_cleaner: ResourceCleaner | None = None
    model_gateway: LLMGateway | None = None
    real_model_enabled: bool = False
    model_fixture_path: str | Path = DEFAULT_MODEL_FIXTURE_PATH

    @property
    def pipeline_id(self) -> str:
        """返回稳定 pipeline id."""
        return PIPELINE_ID

    def describe_nodes(self) -> tuple[NodeDescriptor, ...]:
        """返回由生产侧维护的节点目录."""
        return build_png_to_shader_v1_descriptors()

    def route_deciders(self) -> dict[str, RouteDecider]:
        """暴露两个与 Graph 共用的纯路由 capability."""
        return {
            "decide-after-render": decide_after_render,
            "decide-after-selection": decide_after_selection,
        }

    def source_paths(self) -> tuple[str, ...]:
        """返回 AI-off benchmark 必须冻结的生产源文件."""
        paths = {
            *Path(ROOT / "src/agent/app/nodes/png_to_shader_v1").rglob("*.py"),
            *Path(ROOT / "src/agent/app/prompts").glob("*.yaml"),
            *Path(ROOT / "src/agent/app/prompts").glob("*.py"),
            *Path(ROOT / "src/agent/app/parsers").glob("*.py"),
            ROOT / "src/agent/app/graphs/png_to_shader_v1_routing.py",
        }
        return tuple(
            path.relative_to(ROOT).as_posix()
            for path in sorted(paths)
            if path.is_file()
        )

    def bind(self, host: NodeExecutionHost) -> tuple[NodeExecutorBinding, ...]:
        """按 descriptor 声明自动产生全部精确执行绑定."""
        deterministic = DeterministicNodeExecutor(
            host,
            renderer_factory=self.renderer_factory,
            memory_reader=self.memory_reader,
            resource_cleaner=self.resource_cleaner,
        )
        model = ModelRoleExecutor(
            host,
            gateway=self.model_gateway,
            real_model_enabled=self.real_model_enabled,
            fixture_path=self.model_fixture_path,
        )
        bindings: list[NodeExecutorBinding] = []
        for descriptor in self.describe_nodes():
            if descriptor.requires_model:
                bindings.extend(
                    NodeExecutorBinding(
                        node_id=descriptor.node_id,
                        execution_mode=mode,
                        executor=model,
                    )
                    for mode in MODEL_EXECUTION_MODES
                )
            else:
                bindings.append(
                    NodeExecutorBinding(
                        node_id=descriptor.node_id,
                        execution_mode="deterministic",
                        executor=deterministic,
                    )
                )
        return tuple(bindings)


def create_png_to_shader_v1_node_provider(
    *,
    renderer_factory: RendererFactory = default_renderer_factory,
    memory_reader: MemoryReader | None = None,
    resource_cleaner: ResourceCleaner | None = None,
    model_gateway: LLMGateway | None = None,
    real_model_enabled: bool = False,
    model_fixture_path: str | Path = DEFAULT_MODEL_FIXTURE_PATH,
) -> PngToShaderV1NodeProvider:
    """创建可直接注入 NodeLabApplication 的生产 Provider."""
    return PngToShaderV1NodeProvider(
        renderer_factory=renderer_factory,
        memory_reader=memory_reader,
        resource_cleaner=resource_cleaner,
        model_gateway=model_gateway,
        real_model_enabled=real_model_enabled,
        model_fixture_path=model_fixture_path,
    )


def build_png_to_shader_v1_registry() -> NodeRegistry:
    """构造供生产图一致性测试使用的通用 Registry."""
    return NodeRegistry(build_png_to_shader_v1_descriptors())


__all__ = [
    "DEFAULT_MODEL_FIXTURE_PATH",
    "DeterministicNodeExecutor",
    "MODEL_EXECUTION_MODES",
    "MemoryReader",
    "ModelRoleExecutor",
    "PngToShaderV1NodeProvider",
    "ResourceCleaner",
    "build_png_to_shader_v1_registry",
    "create_png_to_shader_v1_node_provider",
]
