"""Node Lab 到 PNG-to-Shader V2 production callables 的安全适配层。."""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any, Protocol, cast

from pydantic import BaseModel

from agent.app.lab.adapters import RendererFactory, default_renderer_factory
from agent.app.lab.integration import NodeExecutionHost
from agent.app.lab.models import (
    ExecutionMode,
    NodeDescriptor,
    NodeExecutionResult,
    NodeLabError,
    StepExecutionRequest,
    ensure_json_object,
)
from agent.app.nodes.png_to_shader_v2.integrations.node_lab.catalog import (
    NodeLabArtifactCatalogV2,
    collect_artifact_refs,
)
from shaderforge.contracts import REQUIRED_LAYER_ORDER
from shaderforge.intent import (
    LayerHypothesis,
    PrimitiveCandidate,
    RequiredLayerAssessment,
    StrategyHypothesis,
    VisualInterpretationV2,
    parse_visual_interpretation_v2,
)
from shaderforge.store import ArtifactCatalog, ArtifactRefV2

V2_INTERPRETATION_FIXTURE_ID = "visual-interpretation-v2-success-v1"


class InterpretationProvider(Protocol):
    """真实模型组合根必须实现的已审计 Interpretation 物化边界。."""

    def __call__(self, state: Any, catalog: ArtifactCatalog) -> ArtifactRefV2:
        """返回已登记的 strict VisualInterpretationV2 ref。."""


class IntentContextProvider(Protocol):
    """由调用方冻结 catalog 身份并构造 IntentBuildContext 的 production 边界。."""

    def __call__(
        self,
        state: Any,
        measurements: Any,
        interpretation: Any,
        constraints: Any,
    ) -> Any:
        """返回与本 run 输入证据绑定的 IntentBuildContext。."""


class ReferenceArtifactProvider(Protocol):
    """为 Basic Oracle 返回同 run 的规范化参考图 ref。."""

    def __call__(self, state: Any, resolver: Any) -> ArtifactRefV2:
        """返回经过 resolver 完整性校验的 reference PNG ref。."""


ProductionCallable = Callable[[Any], Mapping[str, Any] | Awaitable[Mapping[str, Any]]]
ProductionCallableFactory = Callable[[Any], Mapping[str, ProductionCallable]]


def _json_default(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"无法把 {type(value).__name__} 投影为 JSON。")


def _json_object(value: object, *, path: str) -> dict[str, Any]:
    return ensure_json_object(
        json.loads(
            json.dumps(
                value,
                default=_json_default,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        ),
        path=path,
    )


def _fixture_interpretation(state: Any, catalog: ArtifactCatalog) -> ArtifactRefV2:
    """生成固定、无外部调用的最小合法模型响应 Fixture。.

    Fixture 只提供模型响应形状，仍由 production analyze node 精确恢复和校验；
    它不会执行 Intent Builder、路由、Compiler 或 Selector 语义。
    """
    evidence_ref = cast(ArtifactRefV2, state.measurements_ref)
    assessments = tuple(
        RequiredLayerAssessment(
            layer=layer,
            status="required" if layer == "base_fill" else "not_required",
            confidence=1.0,
            rationale="Node Lab 冻结 Fixture：只声明基础填充层。",
            evidence_refs=(evidence_ref,),
        )
        for layer in REQUIRED_LAYER_ORDER
    )
    interpretation = VisualInterpretationV2(
        summary="Node Lab V2 离线 Fixture：单一基础填充层。",
        layer_hypotheses=(
            LayerHypothesis(
                layer_id="fixture-base-fill",
                role="base_fill",
                order=0,
                confidence=1.0,
                region_description="目标主体区域",
                primitive_candidates=("ellipse_sdf",),
                evidence_refs=(evidence_ref,),
            ),
        ),
        required_layer_assessments=assessments,
        primitive_candidates=(
            PrimitiveCandidate(
                candidate_id="fixture-primitive-ellipse",
                primitive_id="ellipse_sdf",
                layer_id="fixture-base-fill",
                confidence=1.0,
                evidence_refs=(evidence_ref,),
            ),
        ),
        strategy_hypotheses=(
            StrategyHypothesis(
                strategy_id="fixture-strategy-minimal",
                template_ids=("geometry.ellipse_sdf.v0",),
                required_layer_ids=("fixture-base-fill",),
                complexity="low",
                confidence=1.0,
                evidence_refs=(evidence_ref,),
            ),
        ),
        evidence_refs=(evidence_ref,),
    )
    return catalog.put(
        run_id=str(state.run_id),
        kind="visual_interpretation",
        schema_version="visual_interpretation_v2_1",
        content_type="application/json",
        data=interpretation.model_dump_json().encode("utf-8"),
    )


class V2ProductionNodeExecutor:
    """单步构造 run-scoped runtime，并调用 Graph 共用的 production node。."""

    def __init__(
        self,
        host: NodeExecutionHost,
        *,
        execution_mode: ExecutionMode,
        renderer_factory: RendererFactory = default_renderer_factory,
        intent_context_provider: IntentContextProvider,
        reference_artifact_provider: ReferenceArtifactProvider,
        real_interpretation_provider: InterpretationProvider | None = None,
        real_model_enabled: bool = False,
        callable_factory: ProductionCallableFactory | None = None,
    ) -> None:
        """注入 Lab host、受控副作用依赖、执行模式与可测试 callable factory。."""
        self._host = host
        self._execution_mode = execution_mode
        self._renderer_factory = renderer_factory
        self._intent_context_provider = intent_context_provider
        self._reference_artifact_provider = reference_artifact_provider
        self._real_interpretation_provider = real_interpretation_provider
        self._real_model_enabled = real_model_enabled
        self._callable_factory = callable_factory

    def resolve_inputs(
        self,
        descriptor: NodeDescriptor,
        request: StepExecutionRequest,
    ) -> dict[str, object]:
        """业务输入只来自不可变父快照和显式 request.inputs。."""
        del descriptor, request
        return {}

    def preflight(
        self,
        descriptor: NodeDescriptor,
        request: StepExecutionRequest,
    ) -> None:
        """项目写入和真实模型调用均在分配步骤/写 Artifact 前拒绝。."""
        if request.effect_mode == "project_commit":
            raise NodeLabError(
                "effect_not_allowed",
                "Node Lab V2 禁止写入真实项目数据。",
                stage="effect_policy",
                lab_run_id=request.lab_run_id,
                node_id=descriptor.node_id,
            )
        if self._execution_mode != "real":
            return
        if request.preview_only or request.effect_mode == "preview":
            return
        raise NodeLabError(
            "real_model_requires_durable_service",
            "V2 Node Lab 禁止直接调用真实模型；请由 durable Service 预物化 receipt/audit。",
            stage="real_model_gate",
            lab_run_id=request.lab_run_id,
            node_id=descriptor.node_id,
            details={
                "server_enabled": self._real_model_enabled,
                "request_allowed": request.allow_model_call,
            },
        )

    def _mock_provider(
        self,
        request: StepExecutionRequest,
    ) -> InterpretationProvider:
        artifact_id = request.mock_response_artifact_id
        if not artifact_id:
            raise NodeLabError(
                "mock_response_missing",
                "V2 mock 模式必须提供 mock_response_artifact_id。",
                stage="model_mock",
                lab_run_id=request.lab_run_id,
                node_id=request.node_id,
            )
        try:
            _descriptor, payload = self._host.read_artifact(
                request.lab_run_id, artifact_id
            )
            text = payload.decode("utf-8")
            interpretation = parse_visual_interpretation_v2(text)
        except NodeLabError:
            raise
        except (UnicodeDecodeError, ValueError) as exc:
            raise NodeLabError(
                "mock_response_invalid",
                "V2 mock Artifact 不是合法 VisualInterpretationV2。",
                stage="model_mock",
                lab_run_id=request.lab_run_id,
                node_id=request.node_id,
                details={"error_type": type(exc).__name__},
            ) from exc

        def provide(state: Any, catalog: ArtifactCatalog) -> ArtifactRefV2:
            return catalog.put(
                run_id=str(state.run_id),
                kind="visual_interpretation",
                schema_version="visual_interpretation_v2_1",
                content_type="application/json",
                data=interpretation.model_dump_json().encode("utf-8"),
            )

        return provide

    def _interpretation_provider(
        self,
        request: StepExecutionRequest,
    ) -> InterpretationProvider | None:
        if request.preview_only or request.effect_mode == "preview":
            return None
        if self._execution_mode == "fixture":
            if request.fixture_id not in {None, V2_INTERPRETATION_FIXTURE_ID}:
                raise NodeLabError(
                    "fixture_not_found",
                    "V2 analyze 节点不支持该 Fixture id。",
                    stage="fixture_resolution",
                    lab_run_id=request.lab_run_id,
                    node_id=request.node_id,
                )
            return _fixture_interpretation
        if self._execution_mode == "mock":
            return self._mock_provider(request)
        if self._execution_mode == "real":
            return self._real_interpretation_provider
        return None

    def _production_callables(self, runtime: Any) -> Mapping[str, ProductionCallable]:
        if self._callable_factory is not None:
            return self._callable_factory(runtime)
        # 延迟导入确保只 discovery descriptor 不会构造 Graph、Renderer 或模型依赖。
        from agent.app.nodes.png_to_shader_v2.runtime import (
            build_png_to_shader_v2_node_callables,
        )

        return cast(
            Mapping[str, ProductionCallable],
            build_png_to_shader_v2_node_callables(runtime),
        )

    async def execute(
        self,
        descriptor: NodeDescriptor,
        request: StepExecutionRequest,
        state: Mapping[str, object],
    ) -> NodeExecutionResult:
        """通过安全 Adapter 注入副作用边界，并执行同一 production callable。."""
        self.preflight(descriptor, request)
        if request.preview_only or request.effect_mode == "preview":
            return NodeExecutionResult(
                # preview 不执行业务 callable，但仍返回完整未变 State，满足同一
                # descriptor 输出形状且不会把 transport 控制字段写入 State。
                output_patch=_json_object(state, path="$.preview_state"),
                diagnostics={
                    "preview_only": True,
                    "production_callable_invoked": False,
                    "model_call_allowed": False,
                },
                provenance={
                    "execution_source": "v2_model_boundary_preview",
                    "node_id": descriptor.node_id,
                },
                usage={"model_call_count": 0, "browser_launch_count": 0},
            )
        try:
            from agent.app.nodes.png_to_shader_v2.runtime import (
                PngToShaderV2NodeRuntime,
                build_png_to_shader_v2_fixture_runtime,
            )
            from agent.app.states.png_to_shader_v2_state import PngToShaderV2State

            typed_state = PngToShaderV2State.model_validate_json(
                json.dumps(
                    state,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                strict=True,
            )
            catalog = NodeLabArtifactCatalogV2(
                self._host,
                lab_run_id=request.lab_run_id,
                run_id=typed_state.run_id,
                refs=collect_artifact_refs(state),
            )
            browser_launch_count = 0

            def create_renderer(_state: Any) -> Any:
                nonlocal browser_launch_count
                browser_launch_count += 1
                return self._renderer_factory()

            def provide_reference(state_value: Any, resolver: Any) -> ArtifactRefV2:
                ref = self._reference_artifact_provider(state_value, resolver)
                # reference ref 不属于 State 字段；由显式 provider 返回后登记完整
                # 元数据，仍不允许 Catalog 根据 payload 或路径猜 schema。
                catalog.seed_ref(ref)
                return ref

            runtime = build_png_to_shader_v2_fixture_runtime(
                catalog_factory=lambda _state: catalog,
                interpretation_provider=self._interpretation_provider(request),
                intent_context_provider=self._intent_context_provider,
                renderer_factory=create_renderer,
                reference_artifact_provider=provide_reference,
            )
            assert isinstance(runtime, PngToShaderV2NodeRuntime)
            node = self._production_callables(runtime)[descriptor.node_id]
            patch = node(typed_state)
            if inspect.isawaitable(patch):
                patch = await patch
            output = _json_object(patch, path="$.output_patch")
        except NodeLabError:
            raise
        except KeyError as exc:
            raise NodeLabError(
                "node_adapter_not_implemented",
                "V2 Provider 未找到对应 production callable。",
                stage="adapter_dispatch",
                lab_run_id=request.lab_run_id,
                node_id=descriptor.node_id,
            ) from exc
        except (TypeError, ValueError) as exc:
            raise NodeLabError(
                "input_contract_invalid",
                "V2 Node 输入或 Artifact 闭包不符合 production 契约。",
                stage="production_node",
                lab_run_id=request.lab_run_id,
                node_id=descriptor.node_id,
                details={"error_type": type(exc).__name__},
            ) from exc
        model_call_count = (
            1
            if descriptor.requires_model
            and self._execution_mode == "real"
            and state.get("visual_interpretation_ref") is None
            and output.get("visual_interpretation_ref") is not None
            else 0
        )
        return NodeExecutionResult(
            output_patch=output,
            artifacts=list(catalog.created_descriptors),
            provenance={
                "execution_source": "production_node",
                "pipeline_id": "png_to_shader_v2",
                "node_id": descriptor.node_id,
                "model_boundary": self._execution_mode,
                "production_admission_enabled": False,
                "project_promotion_enabled": False,
            },
            usage={
                "model_call_count": model_call_count,
                "browser_launch_count": browser_launch_count,
            },
        )


__all__ = [
    "InterpretationProvider",
    "IntentContextProvider",
    "ProductionCallableFactory",
    "ReferenceArtifactProvider",
    "V2ProductionNodeExecutor",
    "V2_INTERPRETATION_FIXTURE_ID",
]
