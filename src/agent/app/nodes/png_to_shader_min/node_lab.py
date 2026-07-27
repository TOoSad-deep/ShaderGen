"""把当前 scene_mvp 生产 Node 接入通用 Node Lab."""

from __future__ import annotations

import inspect
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from agent.app.config.png_to_shader_min import MIN_PIPELINE_CONFIG
from agent.app.contracts.llm import LLMCallOptions, LLMGateway, LLMResponse
from agent.app.nodes.png_to_shader_min.runtime import (
    MinRendererRegistry,
    RendererFactory,
)
from agent.app.nodes.png_to_shader_min.shader_graph_runtime import (
    ShaderGraphCandidateSnapshot,
    make_shader_graph_nodes,
)
from nodelab.integration import (
    NodeExecutionHost,
    NodeExecutor,
    NodeExecutorBinding,
    NodeProvider,
)
from nodelab.models import (
    ArtifactDescriptor,
    ExecutionMode,
    NodeDescriptor,
    NodeExecutionResult,
    NodeInputExample,
    NodeLabError,
    StepExecutionRequest,
    ensure_json_object,
)
from shaderforge.dsl import ShaderDocument, compile_dsl_shader
from shaderforge.optimization import dsl_parameter_specs
from shaderforge.rendering import GraphProgramKey, PlaywrightWebGL1Renderer
from shaderforge.store import ArtifactRef

SCENE_MVP_NODE_LAB_PIPELINE_ID = "scene_mvp"
_REFERENCE_ARTIFACT_FIELD = "source_artifact_id"
_TARGET_RGB_ARTIFACT_FIELD = "target_rgb_artifact_id"
_CURRENT_RENDER_ARTIFACT_FIELD = "current_render_artifact_id"
_SNAPSHOT_SCHEMA_VERSION = "scene_mvp_node_lab_snapshot_v1"


@dataclass(frozen=True, slots=True)
class _NodeSpec:
    node_id: str
    category: str
    summary: str
    required_inputs: tuple[str, ...]
    base_step_node_id: str | None = None
    side_effects: tuple[str, ...] = ()
    requires_browser: bool = False
    requires_model: bool = False


_NODE_SPECS = (
    _NodeSpec(
        "initialize_run",
        "run_lifecycle",
        "登记参考图并冻结 scene_mvp 运行预算。",
        (_REFERENCE_ARTIFACT_FIELD, "quality_preset"),
        side_effects=("artifact_write",),
    ),
    _NodeSpec(
        "perceive_target",
        "perception",
        "从参考图生成确定性感知结果与 ShaderGraph fallback。",
        (_REFERENCE_ARTIFACT_FIELD,),
        base_step_node_id="initialize_run",
        side_effects=("artifact_read", "artifact_write"),
    ),
    _NodeSpec(
        "author_initial",
        "model_role",
        "生成初始 ShaderDocument；deterministic 模式强制使用感知 fallback。",
        (_REFERENCE_ARTIFACT_FIELD, "fallback_shader_graph"),
        base_step_node_id="perceive_target",
        side_effects=("model_call",),
        requires_model=True,
    ),
    _NodeSpec(
        "materialize_shader",
        "compiler",
        "将当前 ShaderDocument 编译为 specialized WebGL1 GLSL。",
        ("scene",),
        base_step_node_id="author_initial",
    ),
    _NodeSpec(
        "render_and_evaluate",
        "render",
        "真实渲染 ShaderGraph 候选并计算复合损失。",
        (
            "scene",
            _TARGET_RGB_ARTIFACT_FIELD,
            "render_budget",
            "project_id",
            "run_id",
        ),
        base_step_node_id="materialize_shader",
        side_effects=("browser", "artifact_write"),
        requires_browser=True,
    ),
    _NodeSpec(
        "decide_after_render",
        "routing",
        "根据渲染结果、目标和预算决定下一动作。",
        ("render_count", "render_budget", "target_loss"),
        base_step_node_id="render_and_evaluate",
    ),
    _NodeSpec(
        "optimize_base",
        "optimization",
        "对 ShaderGraph canvas 参数块执行有界局部优化。",
        (
            "current_best",
            _TARGET_RGB_ARTIFACT_FIELD,
            "render_count",
            "render_budget",
        ),
        base_step_node_id="decide_after_render",
        side_effects=("browser", "artifact_write"),
        requires_browser=True,
    ),
    _NodeSpec(
        "decide_after_base",
        "routing",
        "根据 base 优化结果、参数队列和预算决定下一动作。",
        (
            "current_best_loss",
            "target_loss",
            "render_count",
            "render_budget",
        ),
        base_step_node_id="optimize_base",
    ),
    _NodeSpec(
        "optimize_feature",
        "optimization",
        "对下一个 ShaderGraph layer/node 参数块执行有界局部优化。",
        (
            "current_best",
            _TARGET_RGB_ARTIFACT_FIELD,
            "feature_queue",
            "render_count",
            "render_budget",
        ),
        base_step_node_id="decide_after_base",
        side_effects=("browser", "artifact_write"),
        requires_browser=True,
    ),
    _NodeSpec(
        "decide_after_feature",
        "routing",
        "根据参数队列、模型预算和渲染预算决定下一动作。",
        (
            "current_best_loss",
            "target_loss",
            "render_count",
            "render_budget",
        ),
        base_step_node_id="optimize_feature",
    ),
    _NodeSpec(
        "author_refine",
        "model_role",
        "生成 typed layer patch；deterministic 模式只保留 current_best。",
        ("current_best", _REFERENCE_ARTIFACT_FIELD),
        base_step_node_id="decide_after_feature",
        side_effects=("model_call",),
        requires_model=True,
    ),
    _NodeSpec(
        "finalize",
        "run_lifecycle",
        "固化当前最佳 ShaderGraph、GLSL、Render、指标和 manifest。",
        ("current_best", "project_id", "run_id"),
        base_step_node_id="decide_after_feature",
        side_effects=("artifact_write", "browser_close"),
        requires_browser=True,
    ),
)
_SPEC_BY_ID = {spec.node_id: spec for spec in _NODE_SPECS}
_MODEL_NODE_IDS = frozenset(
    spec.node_id for spec in _NODE_SPECS if spec.requires_model
)


class _DisabledGateway:
    """AI-off Executor 的 fail-closed Gateway."""

    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        options: LLMCallOptions,
    ) -> LLMResponse:
        del messages, options
        raise RuntimeError("AI-off Node Lab Executor 禁止调用真实模型。")


def _object_schema(required: Iterable[str] = ()) -> dict[str, object]:
    required_fields = list(required)
    return {
        "type": "object",
        "properties": {name: {} for name in required_fields},
        "required": required_fields,
        "additionalProperties": True,
    }


def _example_for(spec: _NodeSpec) -> NodeInputExample:
    if spec.node_id == "initialize_run":
        return NodeInputExample(
            example_id="initialize-run-reference",
            summary="上传参考 PNG 后创建 AI-off fast 运行。",
            execution_mode="deterministic",
            inputs={
                _REFERENCE_ARTIFACT_FIELD: "replace-with-uploaded-artifact-id",
                "quality_preset": "fast",
                "instruction": "复刻参考图的形状、颜色和高光。",
                "llm_budget": 0,
                "refine_budget": 0,
            },
            artifact_inputs={_REFERENCE_ARTIFACT_FIELD: "reference_png"},
        )
    inputs: dict[str, object] = {}
    artifact_inputs: dict[str, str] = {}
    if spec.node_id in ("perceive_target", "author_initial", "author_refine"):
        inputs[_REFERENCE_ARTIFACT_FIELD] = "replace-with-reference-artifact-id"
        artifact_inputs[_REFERENCE_ARTIFACT_FIELD] = "reference_png"
    if spec.node_id in ("render_and_evaluate", "optimize_base", "optimize_feature"):
        inputs[_TARGET_RGB_ARTIFACT_FIELD] = "replace-with-target-rgb-artifact-id"
        artifact_inputs[_TARGET_RGB_ARTIFACT_FIELD] = "target_rgb_npy"
    return NodeInputExample(
        example_id=f"{spec.node_id}-from-parent",
        summary=f"从 {spec.base_step_node_id or 'LabRun root'} 的不可变快照执行。",
        execution_mode="deterministic",
        base_step_node_id=spec.base_step_node_id,
        inputs=inputs,
        artifact_inputs=artifact_inputs,
    )


def _descriptor(spec: _NodeSpec) -> NodeDescriptor:
    modes: list[ExecutionMode] = ["deterministic"]
    if spec.requires_model:
        modes.append("real")
    source_name = (
        "shader_graph_runtime.py"
        if spec.node_id
        in {
            "author_initial",
            "materialize_shader",
            "render_and_evaluate",
            "optimize_base",
            "optimize_feature",
            "author_refine",
            "finalize",
        }
        else "runtime.py"
    )
    return NodeDescriptor(
        pipeline_id=SCENE_MVP_NODE_LAB_PIPELINE_ID,
        node_id=spec.node_id,
        category=spec.category,
        summary=spec.summary,
        prerequisites=list(spec.required_inputs),
        side_effects=list(spec.side_effects),
        implementation_status="available",
        execution_modes=modes,
        test_profiles=["node_lab", "scene_mvp"],
        benchmark_profiles=["node"],
        benchmark_metrics=["schema_pass", "duration_ms"],
        cold_start_sensitive=spec.requires_browser,
        requires_browser=spec.requires_browser,
        requires_model=spec.requires_model,
        source_ref=(
            "src/agent/app/nodes/png_to_shader_min/"
            f"{source_name}#{spec.node_id}"
        ),
        input_schema=_object_schema(spec.required_inputs),
        output_schema=_object_schema(),
        input_examples=[_example_for(spec)],
    )


def _json_value(value: Any) -> Any:
    """把生产对象投影为严格 JSON 值，二进制必须先转 Artifact."""
    if isinstance(value, BaseModel):
        return _json_value(value.model_dump(mode="json", by_alias=True))
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, Enum):
        return _json_value(value.value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("生产 Node 输出包含非有限浮点数。")
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview, np.ndarray)):
        raise TypeError(f"{type(value).__name__} 必须投影为 Node Lab Artifact。")
    raise TypeError(f"无法把生产类型 {type(value).__name__} 投影为 JSON。")


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            _json_value(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _artifact_kind(logical_path: str) -> str:
    path = logical_path.lower()
    if path.endswith("reference.png"):
        return "reference_png"
    if path.endswith("webgl1.glsl"):
        return "final_glsl"
    if path.endswith("render.png"):
        return "final_render_png"
    if path.endswith("shader-graph.json"):
        return "final_shader_graph"
    if path.endswith("metrics.json"):
        return "final_metrics"
    if path.endswith("manifest.json"):
        return "final_manifest"
    return "scene_mvp_node_artifact"


class _LabRunWriter:
    """以 LabRun 不透明 Artifact 实现生产 RunArtifactStore 写接口."""

    def __init__(self, store: _LabArtifactStore) -> None:
        self._store = store

    def write_bytes(
        self,
        relative_path: str | Path,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> ArtifactRef:
        logical_path = Path(relative_path)
        if logical_path.is_absolute() or ".." in logical_path.parts:
            raise ValueError("生产 Artifact 逻辑路径必须位于 run 根内。")
        descriptor = self._store.upload(
            kind=_artifact_kind(logical_path.as_posix()),
            content_type=content_type,
            data=data,
        )
        return ArtifactRef(
            relative_path=descriptor.artifact_id,
            sha256=descriptor.sha256,
            size_bytes=descriptor.size_bytes,
            content_type=descriptor.content_type,
        )

    def write_text(
        self,
        relative_path: str | Path,
        text: str,
        *,
        content_type: str = "text/plain; charset=utf-8",
    ) -> ArtifactRef:
        return self.write_bytes(
            relative_path,
            text.encode("utf-8"),
            content_type=content_type,
        )

    def write_json(self, relative_path: str | Path, value: Any) -> ArtifactRef:
        return self.write_bytes(
            relative_path,
            _json_bytes(value),
            content_type="application/json; charset=utf-8",
        )


class _LabArtifactStore:
    """把生产 Artifact Store 绑定到一个不可变 LabRun namespace."""

    def __init__(self, host: NodeExecutionHost, lab_run_id: str) -> None:
        self._host = host
        self.lab_run_id = lab_run_id
        self.created: list[ArtifactDescriptor] = []
        self._writer = _LabRunWriter(self)

    def _validate_identity(self, project_id: str, run_id: str) -> None:
        lab_run = self._host.get_run(self.lab_run_id)
        if run_id != self.lab_run_id:
            raise NodeLabError(
                "run_identity_mismatch",
                "scene_mvp run_id 必须绑定当前 LabRun。",
                stage="artifact_binding",
                lab_run_id=self.lab_run_id,
            )
        if lab_run.project_id is not None and project_id != lab_run.project_id:
            raise NodeLabError(
                "project_identity_mismatch",
                "scene_mvp project_id 与当前 LabRun 不一致。",
                stage="artifact_binding",
                lab_run_id=self.lab_run_id,
            )

    def register_run(self, project_id: str, run_id: str) -> _LabRunWriter:
        self._validate_identity(project_id, run_id)
        return self._writer

    def start_run(self, project_id: str, run_id: str) -> _LabRunWriter:
        self._validate_identity(project_id, run_id)
        return self._writer

    def upload(self, *, kind: str, content_type: str, data: bytes) -> ArtifactDescriptor:
        descriptor = self._host.upload_artifact(
            lab_run_id=self.lab_run_id,
            kind=kind,
            content_type=content_type,
            data=data,
        )
        self.created.append(descriptor)
        return descriptor


def _active_block(snapshot: ShaderGraphCandidateSnapshot) -> str | None:
    if snapshot.compiled.resource_summary.active_parameter_count <= 0:
        return None
    prefix = "parameter:"
    if not snapshot.provenance.startswith(prefix):
        raise ValueError("带 active uniform 的候选缺少参数 provenance。")
    path = snapshot.provenance.removeprefix(prefix)
    for spec in dsl_parameter_specs(snapshot.document):
        if spec.path == path:
            return spec.block
    raise ValueError("候选 provenance 引用了未知 ShaderGraph 参数。")


def _snapshot_payload(
    snapshot: ShaderGraphCandidateSnapshot,
    store: _LabArtifactStore,
) -> dict[str, Any]:
    render = store.upload(
        kind="candidate_render_png",
        content_type="image/png",
        data=snapshot.render,
    )
    return {
        "schema_version": _SNAPSHOT_SCHEMA_VERSION,
        "document": snapshot.public_document(),
        "document_sha256": snapshot.compiled.document_sha256,
        "glsl_sha256": snapshot.compiled.glsl_sha256,
        "active_block": _active_block(snapshot),
        "program_key": _json_value(snapshot.program_key),
        "mae": snapshot.mae,
        "loss": snapshot.loss,
        "metrics": _json_value(snapshot.metrics),
        "residual_summary": _json_value(snapshot.residual_summary),
        "render_artifact_id": render.artifact_id,
        "parent_document_sha256": snapshot.parent_document_sha256,
        "provenance": snapshot.provenance,
    }


def _hydrate_snapshot(
    value: object,
    host: NodeExecutionHost,
    lab_run_id: str,
) -> ShaderGraphCandidateSnapshot:
    if not isinstance(value, Mapping) or value.get("schema_version") != (
        _SNAPSHOT_SCHEMA_VERSION
    ):
        raise NodeLabError(
            "snapshot_contract_invalid",
            "current_best 不是受支持的 scene_mvp Node Lab 快照。",
            stage="state_hydration",
            lab_run_id=lab_run_id,
        )
    document = ShaderDocument.model_validate(value.get("document"))
    active_block_value = value.get("active_block")
    active_block = str(active_block_value) if active_block_value is not None else None
    compiled = compile_dsl_shader(document, active_block=active_block)
    if value.get("document_sha256") != compiled.document_sha256:
        raise NodeLabError(
            "snapshot_integrity_failed",
            "current_best 文档指纹不一致。",
            stage="state_hydration",
            lab_run_id=lab_run_id,
        )
    if value.get("glsl_sha256") != compiled.glsl_sha256:
        raise NodeLabError(
            "snapshot_integrity_failed",
            "current_best GLSL 指纹不一致。",
            stage="state_hydration",
            lab_run_id=lab_run_id,
        )
    artifact_id = value.get("render_artifact_id")
    if not isinstance(artifact_id, str):
        raise NodeLabError(
            "snapshot_contract_invalid",
            "current_best 缺少 Render Artifact。",
            stage="state_hydration",
            lab_run_id=lab_run_id,
        )
    descriptor, render = host.read_artifact(lab_run_id, artifact_id)
    if descriptor.content_type != "image/png":
        raise NodeLabError(
            "snapshot_contract_invalid",
            "current_best Render Artifact 不是 PNG。",
            stage="state_hydration",
            lab_run_id=lab_run_id,
        )
    program_key = GraphProgramKey(
        compiler_version=compiled.compiler_version,
        topology_sha256=compiled.topology_sha256,
        active_parameter_manifest_sha256=compiled.parameter_manifest_sha256,
        baked_parameter_sha256=compiled.glsl_sha256,
        width=document.canvas.width,
        height=document.canvas.height,
    )
    return ShaderGraphCandidateSnapshot(
        document=document,
        compiled=compiled,
        program_key=program_key,
        mae=float(value["mae"]),
        loss=float(value["loss"]),
        metrics=ensure_json_object(value.get("metrics", {})),
        residual_summary=ensure_json_object(value.get("residual_summary", {})),
        render=render,
        parent_document_sha256=(
            str(value["parent_document_sha256"])
            if value.get("parent_document_sha256") is not None
            else None
        ),
        provenance=str(value["provenance"]),
    )


def _hydrate_state(
    state: Mapping[str, object],
    host: NodeExecutionHost,
    lab_run_id: str,
) -> dict[str, Any]:
    hydrated: dict[str, Any] = dict(state)
    source_artifact_id = state.get(_REFERENCE_ARTIFACT_FIELD)
    if isinstance(source_artifact_id, str):
        descriptor, image = host.read_artifact(lab_run_id, source_artifact_id)
        if not descriptor.content_type.startswith("image/"):
            raise NodeLabError(
                "reference_artifact_invalid",
                "参考 Artifact 必须是图片。",
                stage="state_hydration",
                lab_run_id=lab_run_id,
            )
        hydrated["image"] = image
        hydrated["content_type"] = descriptor.content_type
    target_artifact_id = state.get(_TARGET_RGB_ARTIFACT_FIELD)
    if isinstance(target_artifact_id, str):
        descriptor, payload = host.read_artifact(lab_run_id, target_artifact_id)
        if descriptor.content_type != "application/x-npy":
            raise NodeLabError(
                "target_rgb_artifact_invalid",
                "Target RGB Artifact 类型不受支持。",
                stage="state_hydration",
                lab_run_id=lab_run_id,
            )
        target = np.load(BytesIO(payload), allow_pickle=False)
        if target.ndim != 3 or target.shape[-1] != 3:
            raise NodeLabError(
                "target_rgb_artifact_invalid",
                "Target RGB Artifact 维度不合法。",
                stage="state_hydration",
                lab_run_id=lab_run_id,
            )
        hydrated["target_rgb"] = target
    if "current_best" in state:
        hydrated["current_best"] = _hydrate_snapshot(
            state["current_best"],
            host,
            lab_run_id,
        )
    return hydrated


def _project_output(
    update: Mapping[str, Any],
    store: _LabArtifactStore,
) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    snapshot_value = update.get("current_best")
    snapshot_payload = (
        _snapshot_payload(snapshot_value, store)
        if isinstance(snapshot_value, ShaderGraphCandidateSnapshot)
        else None
    )
    for key, value in update.items():
        if key == "current_best" and snapshot_payload is not None:
            projected[key] = snapshot_payload
            continue
        if key == "current_render" and isinstance(value, bytes):
            if snapshot_payload is not None:
                projected[_CURRENT_RENDER_ARTIFACT_FIELD] = snapshot_payload[
                    "render_artifact_id"
                ]
            else:
                descriptor = store.upload(
                    kind="current_render_png",
                    content_type="image/png",
                    data=value,
                )
                projected[_CURRENT_RENDER_ARTIFACT_FIELD] = descriptor.artifact_id
            continue
        if key == "target_rgb" and isinstance(value, np.ndarray):
            buffer = BytesIO()
            np.save(buffer, value, allow_pickle=False)
            descriptor = store.upload(
                kind="target_rgb_npy",
                content_type="application/x-npy",
                data=buffer.getvalue(),
            )
            projected[_TARGET_RGB_ARTIFACT_FIELD] = descriptor.artifact_id
            continue
        if isinstance(value, bytes):
            descriptor = store.upload(
                kind=f"{key}_binary",
                content_type="application/octet-stream",
                data=value,
            )
            projected[f"{key}_artifact_id"] = descriptor.artifact_id
            continue
        projected[key] = _json_value(value)
    return ensure_json_object(projected, path="$.scene_mvp_output")


class _SceneMvpRuntime:
    """共享同一服务进程内的 Renderer cache 与生产依赖."""

    def __init__(
        self,
        host: NodeExecutionHost,
        *,
        renderer_factory: RendererFactory,
        real_model_enabled: bool,
        model_gateway: LLMGateway | None,
    ) -> None:
        self.host = host
        self.registry = MinRendererRegistry(renderer_factory)
        self.real_model_enabled = real_model_enabled
        if real_model_enabled and model_gateway is None:
            raise ValueError("启用真实模型的 scene_mvp Provider 必须注入 LLMGateway。")
        self.real_gateway = model_gateway or _DisabledGateway()
        self.disabled_gateway = _DisabledGateway()


class _SceneMvpNodeExecutor:
    """执行一个当前生产 Node，并负责 Artifact hydration/projection."""

    def __init__(
        self,
        runtime: _SceneMvpRuntime,
        *,
        force_ai_off: bool,
    ) -> None:
        self._runtime = runtime
        self._force_ai_off = force_ai_off

    def resolve_inputs(
        self,
        descriptor: NodeDescriptor,
        request: StepExecutionRequest,
    ) -> dict[str, object]:
        run = self._runtime.host.get_run(request.lab_run_id)
        defaults: dict[str, object] = {
            "project_id": run.project_id or "node-lab-local",
            "run_id": request.lab_run_id,
        }
        if descriptor.node_id != "initialize_run":
            return defaults
        preset_name = str(request.inputs.get("quality_preset", "fast"))
        try:
            policy = MIN_PIPELINE_CONFIG.quality_presets[preset_name]
        except KeyError as exc:
            raise NodeLabError(
                "quality_preset_invalid",
                "Node Lab 不支持该 scene_mvp 质量档位。",
                stage="input_resolution",
                lab_run_id=request.lab_run_id,
                node_id=descriptor.node_id,
            ) from exc
        return {
            **defaults,
            "quality_preset": preset_name,
            "instruction": "",
            "render_budget": policy.render_budget,
            "llm_budget": 0,
            "refine_budget": 0,
            "target_mae": policy.target_mae,
            "target_loss": policy.target_loss,
            "run_classification": MIN_PIPELINE_CONFIG.run_classification,
            "experiment_id": MIN_PIPELINE_CONFIG.experiment_id,
            "config_fingerprint": MIN_PIPELINE_CONFIG.config_fingerprint,
            "report_schema_version": MIN_PIPELINE_CONFIG.report_schema_version,
        }

    def preflight(
        self,
        descriptor: NodeDescriptor,
        request: StepExecutionRequest,
    ) -> None:
        if request.execution_mode != "real":
            return
        if descriptor.node_id not in _MODEL_NODE_IDS:
            raise NodeLabError(
                "unsupported_execution_mode",
                "只有 scene_mvp 模型节点支持 real 模式。",
                stage="real_model_gate",
                lab_run_id=request.lab_run_id,
                node_id=descriptor.node_id,
            )
        if not self._runtime.real_model_enabled or not request.allow_model_call:
            raise NodeLabError(
                "real_model_not_allowed",
                "真实模型调用未满足服务端和请求双重开关。",
                stage="real_model_gate",
                lab_run_id=request.lab_run_id,
                node_id=descriptor.node_id,
                details={
                    "server_enabled": self._runtime.real_model_enabled,
                    "request_allowed": request.allow_model_call,
                },
            )

    async def execute(
        self,
        descriptor: NodeDescriptor,
        request: StepExecutionRequest,
        state: Mapping[str, object],
    ) -> NodeExecutionResult:
        runtime_state = _hydrate_state(
            state,
            self._runtime.host,
            request.lab_run_id,
        )
        if self._force_ai_off and descriptor.node_id in _MODEL_NODE_IDS:
            runtime_state["llm_budget"] = int(runtime_state.get("llm_call_count", 0))
        gateway = (
            self._runtime.disabled_gateway
            if self._force_ai_off
            else self._runtime.real_gateway
        )
        artifact_store = _LabArtifactStore(self._runtime.host, request.lab_run_id)
        nodes = make_shader_graph_nodes(
            artifact_store,  # type: ignore[arg-type]
            self._runtime.registry,
            gateway,
        )
        node = nodes[descriptor.node_id]
        result = node(runtime_state)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, Mapping):
            raise TypeError("scene_mvp 生产 Node 必须返回 Mapping。")
        projected = _project_output(result, artifact_store)
        before_value = state.get("llm_call_count", 0)
        before_calls = (
            before_value
            if isinstance(before_value, int) and not isinstance(before_value, bool)
            else 0
        )
        after_calls = int(projected.get("llm_call_count", before_calls))
        next_action = projected.get("next_action")
        return NodeExecutionResult(
            output_patch=projected,
            diagnostics={
                "artifact_count": len(artifact_store.created),
                "json_projection": "scene_mvp_node_lab_v1",
            },
            provenance={
                "execution_source": "scene_mvp_production_node",
                "source_ref": descriptor.source_ref,
            },
            usage={
                "model_call_count": max(0, after_calls - before_calls),
                "browser_required": descriptor.requires_browser,
            },
            next_action=str(next_action) if isinstance(next_action, str) else None,
            artifacts=artifact_store.created,
        )


class SceneMvpNodeProvider(NodeProvider):
    """当前 12 节点 scene_mvp 的显式 allowlist Provider."""

    pipeline_id = SCENE_MVP_NODE_LAB_PIPELINE_ID

    def __init__(
        self,
        *,
        renderer_factory: RendererFactory = PlaywrightWebGL1Renderer,
        real_model_enabled: bool = False,
        model_gateway: LLMGateway | None = None,
    ) -> None:
        """冻结 Renderer、模型门禁和可选测试 Gateway."""
        self._renderer_factory = renderer_factory
        self._real_model_enabled = real_model_enabled
        self._model_gateway = model_gateway
        self._descriptors = tuple(_descriptor(spec) for spec in _NODE_SPECS)

    def describe_nodes(self) -> tuple[NodeDescriptor, ...]:
        """返回当前 Graph 的 12 个稳定 Node descriptor."""
        return self._descriptors

    def bind(self, host: NodeExecutionHost) -> Iterable[NodeExecutorBinding]:
        """为当前 Application host 创建共享资源并绑定所有执行模式."""
        runtime = _SceneMvpRuntime(
            host,
            renderer_factory=self._renderer_factory,
            real_model_enabled=self._real_model_enabled,
            model_gateway=self._model_gateway,
        )
        bindings: list[NodeExecutorBinding] = []
        for descriptor in self._descriptors:
            for mode in descriptor.execution_modes:
                executor: NodeExecutor = _SceneMvpNodeExecutor(
                    runtime,
                    force_ai_off=mode != "real",
                )
                bindings.append(
                    NodeExecutorBinding(
                        node_id=descriptor.node_id,
                        execution_mode=mode,
                        executor=executor,
                    )
                )
        return tuple(bindings)

    def source_paths(self) -> Iterable[str]:
        """返回影响 Node Lab 执行语义的生产源码."""
        root = Path(__file__).resolve().parent
        return (
            str(Path(__file__).resolve()),
            str((root / "runtime.py").resolve()),
            str((root / "shader_graph_runtime.py").resolve()),
        )


def create_scene_mvp_node_provider(
    *,
    renderer_factory: RendererFactory = PlaywrightWebGL1Renderer,
    real_model_enabled: bool = False,
    model_gateway: LLMGateway | None = None,
) -> SceneMvpNodeProvider:
    """创建可测试、可注入依赖的当前产品 Node Provider."""
    return SceneMvpNodeProvider(
        renderer_factory=renderer_factory,
        real_model_enabled=real_model_enabled,
        model_gateway=model_gateway,
    )


__all__ = [
    "SCENE_MVP_NODE_LAB_PIPELINE_ID",
    "SceneMvpNodeProvider",
    "create_scene_mvp_node_provider",
]
