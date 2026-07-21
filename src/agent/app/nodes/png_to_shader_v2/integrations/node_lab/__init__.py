"""PNG-to-Shader V2 production Node 向通用 Node Lab 暴露的 Provider。."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent.app.lab.adapters import RendererFactory, default_renderer_factory
from agent.app.lab.integration import NodeExecutionHost, NodeExecutorBinding
from agent.app.lab.models import ExecutionMode, NodeDescriptor
from agent.app.lab.registry import NodeRegistry

from .executor import (
    V2_INTERPRETATION_FIXTURE_ID,
    IntentContextProvider,
    InterpretationProvider,
    ProductionCallableFactory,
    ReferenceArtifactProvider,
    V2ProductionNodeExecutor,
)
from .registry import PIPELINE_ID, build_png_to_shader_v2_descriptors

ROOT = Path(__file__).resolve().parents[7]
MODEL_EXECUTION_MODES: tuple[ExecutionMode, ...] = ("fixture", "mock", "real")


@dataclass(frozen=True)
class PngToShaderV2NodeProvider:
    """正式 V2 Graph 节点的 descriptor 与受控单节点装配入口。."""

    intent_context_provider: IntentContextProvider
    reference_artifact_provider: ReferenceArtifactProvider
    renderer_factory: RendererFactory = default_renderer_factory
    real_interpretation_provider: InterpretationProvider | None = None
    real_model_enabled: bool = False
    callable_factory: ProductionCallableFactory | None = None

    @property
    def pipeline_id(self) -> str:
        """返回与 V1 隔离的稳定 pipeline id。."""
        return PIPELINE_ID

    def describe_nodes(self) -> tuple[NodeDescriptor, ...]:
        """按 production node tuple 顺序返回完整 descriptor。."""
        return build_png_to_shader_v2_descriptors()

    def source_paths(self) -> tuple[str, ...]:
        """返回 Node Lab benchmark 应冻结的 V2 production/Harness 源文件。."""
        paths = {
            *Path(ROOT / "src/agent/app/nodes/png_to_shader_v2").rglob("*.py"),
            ROOT / "src/agent/app/graphs/png_to_shader_v2_routing.py",
            ROOT / "src/agent/app/states/png_to_shader_v2_state.py",
            ROOT / "src/agent/app/prompts/analyze_visual_layers_v2.yaml",
        }
        return tuple(
            path.relative_to(ROOT).as_posix()
            for path in sorted(paths)
            if path.is_file()
        )

    def bind(self, host: NodeExecutionHost) -> tuple[NodeExecutorBinding, ...]:
        """为 deterministic 与三种模型模式建立精确执行绑定。."""
        deterministic = V2ProductionNodeExecutor(
            host,
            execution_mode="deterministic",
            renderer_factory=self.renderer_factory,
            intent_context_provider=self.intent_context_provider,
            reference_artifact_provider=self.reference_artifact_provider,
            callable_factory=self.callable_factory,
        )
        model_executors = {
            mode: V2ProductionNodeExecutor(
                host,
                execution_mode=mode,
                renderer_factory=self.renderer_factory,
                intent_context_provider=self.intent_context_provider,
                reference_artifact_provider=self.reference_artifact_provider,
                real_interpretation_provider=self.real_interpretation_provider,
                real_model_enabled=self.real_model_enabled,
                callable_factory=self.callable_factory,
            )
            for mode in MODEL_EXECUTION_MODES
        }
        bindings: list[NodeExecutorBinding] = []
        for descriptor in self.describe_nodes():
            if descriptor.requires_model:
                bindings.extend(
                    NodeExecutorBinding(
                        node_id=descriptor.node_id,
                        execution_mode=mode,
                        executor=model_executors[mode],
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


def create_png_to_shader_v2_node_provider(
    *,
    intent_context_provider: IntentContextProvider,
    reference_artifact_provider: ReferenceArtifactProvider,
    renderer_factory: RendererFactory = default_renderer_factory,
    real_interpretation_provider: InterpretationProvider | None = None,
    real_model_enabled: bool = False,
    callable_factory: ProductionCallableFactory | None = None,
) -> PngToShaderV2NodeProvider:
    """创建可显式注入 NodeLabApplication 的 V2 production Provider。."""
    return PngToShaderV2NodeProvider(
        intent_context_provider=intent_context_provider,
        reference_artifact_provider=reference_artifact_provider,
        renderer_factory=renderer_factory,
        real_interpretation_provider=real_interpretation_provider,
        real_model_enabled=real_model_enabled,
        callable_factory=callable_factory,
    )


def build_png_to_shader_v2_registry() -> NodeRegistry:
    """构造供 Graph/Provider 一致性测试使用的通用 Registry。."""
    return NodeRegistry(build_png_to_shader_v2_descriptors())


__all__ = [
    "InterpretationProvider",
    "IntentContextProvider",
    "MODEL_EXECUTION_MODES",
    "PIPELINE_ID",
    "PngToShaderV2NodeProvider",
    "ProductionCallableFactory",
    "ReferenceArtifactProvider",
    "V2ProductionNodeExecutor",
    "V2_INTERPRETATION_FIXTURE_ID",
    "build_png_to_shader_v2_descriptors",
    "build_png_to_shader_v2_registry",
    "create_png_to_shader_v2_node_provider",
]
