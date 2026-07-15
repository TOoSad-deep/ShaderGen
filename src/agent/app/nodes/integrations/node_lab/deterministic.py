"""PNG-to-Shader V1 生产 Node 的 Node Lab 确定性适配层."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, is_dataclass
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol, cast

from agent.app.context.builder import ContextPolicy
from agent.app.graphs.png_to_shader_v1_routing import (
    decide_after_render,
    decide_after_selection,
)
from agent.app.lab.adapters import (
    RendererFactory,
    ShaderRenderer,
    default_renderer_factory,
)
from agent.app.lab.models import (
    ArtifactDescriptor,
    LabRunRecord,
    NodeDescriptor,
    NodeExecutionResult,
    NodeLabError,
    StepExecutionRequest,
    ensure_json_object,
)
from agent.app.nodes.integrations.node_lab.registry import (
    build_png_to_shader_v1_descriptors,
)
from agent.app.nodes.png_to_shader_v1_run_nodes import (
    NodeEvidenceError,
    RunRendererRegistry,
    make_finalize_png_to_shader_v1_node,
    make_initialize_png_to_shader_v1_node,
    make_load_current_best_node,
    make_materialize_candidate_node,
    make_measure_target_node,
    make_persist_visual_analysis_node,
    make_persist_visual_review_node,
    make_prepare_compile_repair_node,
    make_prepare_measurement_seed_node,
    make_render_and_evaluate_node,
    make_select_current_best_node,
)
from agent.app.nodes.prepare_context_node import (
    ProjectMemoryReader,
    make_prepare_context_node,
)
from agent.app.nodes.promote_validated_strategy_node import (
    make_preview_validated_strategy_node,
)
from shaderforge.public import (
    ArtifactRef,
    CandidateRecord,
    QualityPreset,
    budget_for_preset,
    evaluate_render,
    measure_target,
)
from shaderforge.store import LocalArtifactStore

SUPPORTED_NODE_IDS = frozenset(
    descriptor.node_id
    for descriptor in build_png_to_shader_v1_descriptors()
    if not descriptor.requires_model
)


class ArtifactAccess(Protocol):
    """生产 Node Executor 所需的不透明 Lab Artifact 边界."""

    def get_run(self, lab_run_id: str) -> LabRunRecord:
        """读取 LabRun 及其 project_id 绑定."""

    def upload_artifact(
        self,
        *,
        lab_run_id: str,
        kind: str,
        content_type: str,
        data: bytes,
    ) -> ArtifactDescriptor:
        """写入一个不透明 Artifact."""

    def read_artifact(
        self,
        lab_run_id: str,
        artifact_id: str,
    ) -> tuple[ArtifactDescriptor, bytes]:
        """读取同一 LabRun 的不透明 Artifact."""


class ResourceCleaner(Protocol):
    """可选的 LabRun 外部资源清理边界."""

    async def close(self, lab_run_id: str) -> None:
        """幂等清理与 LabRun 绑定的资源."""


class MemoryReader(ProjectMemoryReader, Protocol):
    """Node Lab 对生产 Context Node 注入的项目 Memory 只读契约."""


def _json_default(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    raise TypeError(f"无法把 {type(value).__name__} 序列化为 JSON。")


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=_json_default,
        )
        + "\n"
    ).encode("utf-8")


def _json_value(value: Any) -> Any:
    """把生产 Node 的 dataclass/tuple 投影为 JSON-safe 值."""
    return json.loads(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=_json_default,
        )
    )


def _artifact_kind(relative_path: str) -> str:
    """把生产 Node 的逻辑路径收敛为稳定、不含路径的 Artifact kind."""
    path = relative_path.lower()
    if path.endswith("reference.png"):
        return "reference_png"
    if path.endswith("run-config.json"):
        return "run_config"
    if path.endswith("measurements.json"):
        return "target_measurements"
    if path.endswith("visual-analysis.json"):
        return "visual_analysis"
    if path.endswith("selection.json"):
        return "selection_decision"
    if "/reviews/" in path:
        return "visual_review"
    if path.endswith("shader.frag"):
        return "final_glsl" if path.startswith("final/") else "candidate_glsl"
    if path.endswith("author.json"):
        return "candidate_author"
    if path.endswith("provenance.json"):
        return "candidate_provenance"
    if path.endswith("compile.json"):
        return "compile_diagnostics"
    if path.endswith("render.png"):
        return "final_render_png" if path.startswith("final/") else "render_png"
    if path.endswith("metrics.json"):
        return "final_metrics" if path.startswith("final/") else "score_metrics"
    if path.endswith("manifest.json"):
        return "final_manifest" if path.startswith("final/") else "candidate_manifest"
    if path.endswith("source.bin"):
        return "source_copy"
    return "production_node_artifact"


class _LabRunArtifactStore:
    """用不透明 Artifact id 实现生产 Node 所需的 RunArtifactStore 接口."""

    def __init__(self, artifacts: ArtifactAccess, lab_run_id: str) -> None:
        self._artifacts = artifacts
        self._lab_run_id = lab_run_id
        self.created: list[ArtifactDescriptor] = []
        self._by_logical_path: dict[str, ArtifactDescriptor] = {}

    @staticmethod
    def _logical_path(relative_path: str | Path) -> str:
        path = Path(relative_path)
        if path.is_absolute() or not path.parts or ".." in path.parts:
            raise ValueError("Artifact 逻辑路径必须位于 run 根内。")
        return path.as_posix()

    def write_bytes(
        self,
        relative_path: str | Path,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> ArtifactRef:
        logical_path = self._logical_path(relative_path)
        descriptor = self._artifacts.upload_artifact(
            lab_run_id=self._lab_run_id,
            kind=_artifact_kind(logical_path),
            content_type=content_type,
            data=data,
        )
        self.created.append(descriptor)
        self._by_logical_path[logical_path] = descriptor
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

    def read_bytes(self, relative_path: str | Path) -> bytes:
        artifact_id = str(relative_path)
        return self._artifacts.read_artifact(self._lab_run_id, artifact_id)[1]

    def descriptor_for(self, relative_path: str) -> ArtifactDescriptor | None:
        return self._by_logical_path.get(relative_path)

    @property
    def lab_run_id(self) -> str:
        """返回当前不透明 Artifact namespace 的 LabRun id."""
        return self._lab_run_id


class _LabNodeArtifactStore:
    """把生产 LocalArtifactStore 的 run 绑定适配到一个 LabRun."""

    def __init__(
        self,
        artifacts: ArtifactAccess,
        lab_run_id: str,
        run_store: _LabRunArtifactStore,
    ) -> None:
        """注入 Artifact、Renderer、Memory、清理器和可测试时钟."""
        self._artifacts = artifacts
        self._lab_run_id = lab_run_id
        self._run_store = run_store

    def _validate(self, project_id: str, run_id: str) -> None:
        lab_run = self._artifacts.get_run(self._lab_run_id)
        if lab_run.project_id is not None and project_id != lab_run.project_id:
            raise NodeEvidenceError("生产 Node project_id 与 LabRun 绑定不一致。")
        if not project_id.strip() or not run_id.strip():
            raise ValueError("project_id 和 run_id 不能为空。")

    def register_run(self, project_id: str, run_id: str) -> _LabRunArtifactStore:
        self._validate(project_id, run_id)
        return self._run_store

    def start_run(self, project_id: str, run_id: str) -> _LabRunArtifactStore:
        self._validate(project_id, run_id)
        return self._run_store


class DeterministicNodeExecutor:
    """仅做契约适配，并直接执行生产 Node 的确定性 Executor."""

    def __init__(
        self,
        artifacts: ArtifactAccess,
        *,
        renderer_factory: RendererFactory = default_renderer_factory,
        memory_reader: MemoryReader | None = None,
        resource_cleaner: ResourceCleaner | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """注入 Artifact、Renderer、Memory、清理器和可测试时钟."""
        self._artifacts = artifacts
        self._memory_reader = memory_reader
        self._resource_cleaner = resource_cleaner
        self._clock = clock
        self._renderer_factory = renderer_factory

    def resolve_inputs(
        self,
        descriptor: NodeDescriptor,
        request: StepExecutionRequest,
    ) -> dict[str, object]:
        """生产 Node 只消费父快照和显式输入，不注入隐藏业务状态."""
        del descriptor, request
        return {}

    def preflight(
        self,
        descriptor: NodeDescriptor,
        request: StepExecutionRequest,
    ) -> None:
        """所有项目级写入都在分配步骤和写 Artifact 之前 fail closed."""
        if request.effect_mode == "project_commit":
            raise NodeLabError(
                "effect_not_allowed",
                "Node Lab 禁止写入真实项目数据。",
                stage="effect_policy",
                lab_run_id=request.lab_run_id,
                node_id=descriptor.node_id,
            )

    def _input_error(
        self,
        request: StepExecutionRequest,
        field: str,
        message: str,
    ) -> NodeLabError:
        return NodeLabError(
            "input_contract_invalid",
            message,
            stage="node_input",
            lab_run_id=request.lab_run_id,
            node_id=request.node_id,
            details={"field": field},
        )

    def _project_state(
        self,
        request: StepExecutionRequest,
        state: Mapping[str, object],
    ) -> dict[str, Any]:
        result = dict(state)
        run = self._artifacts.get_run(request.lab_run_id)
        supplied = result.get("project_id")
        if run.project_id is not None:
            if supplied is not None and supplied != run.project_id:
                raise NodeLabError(
                    "project_scope_mismatch",
                    "Node 输入 project_id 与 LabRun 绑定不一致。",
                    stage="project_scope",
                    lab_run_id=request.lab_run_id,
                    node_id=request.node_id,
                )
            result["project_id"] = run.project_id
        elif supplied is None:
            # 单节点调用可以不先执行 initialize；该隔离 id 只用于满足生产
            # Node 的 run Artifact namespace，不会写入真实项目数据。
            result["project_id"] = request.lab_run_id
        if "run_id" not in result:
            result["run_id"] = request.lab_run_id
        return result

    def _artifact(
        self,
        request: StepExecutionRequest,
        artifact_id: object,
        *,
        field: str,
    ) -> tuple[ArtifactDescriptor, bytes]:
        if not isinstance(artifact_id, str) or not artifact_id.strip():
            raise self._input_error(request, field, f"{field} 必须是非空 Artifact id。")
        return self._artifacts.read_artifact(request.lab_run_id, artifact_id)

    def _json_artifact(
        self,
        request: StepExecutionRequest,
        state: Mapping[str, Any],
        field: str,
    ) -> dict[str, Any]:
        _descriptor, data = self._artifact(request, state.get(field), field=field)
        try:
            return ensure_json_object(json.loads(data), path=f"$.{field}")
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            raise NodeEvidenceError(f"{field} 不是有效 JSON object Artifact。") from exc

    def _text_artifact(
        self,
        request: StepExecutionRequest,
        state: Mapping[str, Any],
        field: str,
    ) -> str:
        _descriptor, data = self._artifact(request, state.get(field), field=field)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise NodeEvidenceError(f"{field} 不是 UTF-8 文本 Artifact。") from exc

    def _target_measurements(
        self,
        request: StepExecutionRequest,
        state: Mapping[str, Any],
    ) -> tuple[Any, bytes]:
        _reference, reference = self._artifact(
            request,
            state.get("reference_artifact_id"),
            field="reference_artifact_id",
        )
        measurements = measure_target(reference)
        supplied = state.get("target_measurements")
        if isinstance(supplied, Mapping):
            actual = measurements.to_dict()
            mismatched = [
                key
                for key, value in supplied.items()
                if key in actual and _json_value(actual[key]) != _json_value(value)
            ]
            if mismatched:
                raise NodeEvidenceError(
                    "TargetMeasurements 与 reference Artifact 证据绑定不一致。"
                )
        return measurements, reference

    def _hydrate(
        self,
        request: StepExecutionRequest,
        state: Mapping[str, object],
    ) -> dict[str, Any]:
        hydrated = self._project_state(request, state)
        node_id = request.node_id
        if node_id == "initialize_run":
            _source, hydrated["image"] = self._artifact(
                request,
                hydrated.get("source_artifact_id"),
                field="source_artifact_id",
            )
        elif node_id == "measure_target":
            _reference, hydrated["image"] = self._artifact(
                request,
                hydrated.get("reference_artifact_id"),
                field="reference_artifact_id",
            )
        elif node_id == "persist_visual_analysis":
            hydrated["visual_analysis"] = self._json_artifact(
                request, hydrated, "visual_analysis_artifact_id"
            )
        elif node_id == "materialize_candidate":
            hydrated["author_result"] = self._json_artifact(
                request, hydrated, "author_artifact_id"
            )
            hydrated["candidate_provenance"] = self._json_artifact(
                request, hydrated, "candidate_provenance_artifact_id"
            )
            hydrated["glsl"] = self._text_artifact(
                request, hydrated, "glsl_artifact_id"
            )
        elif node_id == "render_and_evaluate":
            shader_field = (
                "shader_artifact_id"
                if hydrated.get("shader_artifact_id")
                else "glsl_artifact_id"
            )
            hydrated["glsl"] = self._text_artifact(request, hydrated, shader_field)
            measurements, reference = self._target_measurements(request, hydrated)
            hydrated["target_measurements"] = measurements
            hydrated["image"] = reference
            if hydrated.get("visual_analysis_artifact_id"):
                hydrated["visual_analysis"] = self._json_artifact(
                    request, hydrated, "visual_analysis_artifact_id"
                )
            hydrated.setdefault(
                "budget_policy", asdict(budget_for_preset(QualityPreset.BALANCED))
            )
            hydrated.setdefault("started_at", self._clock())
        elif node_id == "prepare_compile_repair":
            hydrated["author_result"] = self._json_artifact(
                request, hydrated, "author_artifact_id"
            )
        elif node_id == "prepare_measurement_seed":
            measurements, reference = self._target_measurements(request, hydrated)
            hydrated["target_measurements"] = measurements
            hydrated["image"] = reference
        elif node_id == "persist_visual_review":
            hydrated["visual_review"] = self._json_artifact(
                request, hydrated, "visual_review_artifact_id"
            )
        return hydrated

    async def _invoke(
        self,
        node_id: str,
        state: Mapping[str, Any],
        store: _LabNodeArtifactStore,
        usage: dict[str, int],
    ) -> dict[str, Any]:
        node_store = cast(LocalArtifactStore, store)
        if node_id == "initialize_run":
            return await make_initialize_png_to_shader_v1_node(
                node_store,
                clock=self._clock,
            )(state)
        if node_id == "prepare_context":
            raw_policy = state.get("context_policy")
            policy = (
                ContextPolicy(**raw_policy)
                if isinstance(raw_policy, dict)
                else ContextPolicy()
            )
            return await make_prepare_context_node(
                policy, memory_reader=self._memory_reader
            )(state, None)
        if node_id == "measure_target":
            return await make_measure_target_node(node_store)(state)
        if node_id == "persist_visual_analysis":
            return await make_persist_visual_analysis_node(node_store)(state)
        if node_id == "materialize_candidate":
            return await make_materialize_candidate_node(node_store)(state)
        if node_id == "render_and_evaluate":
            def create_renderer(_replay_on_worker_failure: int) -> ShaderRenderer:
                usage["browser_launch_count"] += 1
                return self._renderer_factory()

            registry = RunRendererRegistry(create_renderer)
            key = str(state["project_id"]), str(state["run_id"])
            try:
                patch = await make_render_and_evaluate_node(
                    node_store,
                    registry,
                    evaluate_render,
                    clock=self._clock,
                )(state)
            except BaseException:
                try:
                    await registry.close(key)
                except Exception:
                    pass
                raise
            try:
                await registry.close(key)
            except Exception as exc:
                raise NodeLabError(
                    "renderer_unavailable",
                    "Node Lab 单步 Renderer 清理失败。",
                    stage="renderer_cleanup",
                    retryable=True,
                    node_id=node_id,
                ) from exc
            return patch
        if node_id == "decide_after_render":
            return decide_after_render(state)
        if node_id == "prepare_compile_repair":
            return await make_prepare_compile_repair_node()(state)
        if node_id == "select_current_best":
            return await make_select_current_best_node(node_store)(state)
        if node_id == "prepare_measurement_seed":
            return await make_prepare_measurement_seed_node()(state)
        if node_id == "decide_after_selection":
            return decide_after_selection(state)
        if node_id == "load_current_best":
            return await make_load_current_best_node(node_store)(state)
        if node_id == "persist_visual_review":
            return await make_persist_visual_review_node(node_store)(state)
        if node_id == "finalize":
            empty_registry = RunRendererRegistry(
                lambda _replay_on_worker_failure: self._renderer_factory()
            )
            return await make_finalize_png_to_shader_v1_node(
                node_store,
                empty_registry,
                clock=self._clock,
            )(state)
        if node_id == "promote_validated_strategy":
            return await make_preview_validated_strategy_node(node_store)(state)
        raise NodeLabError(
            "node_adapter_not_implemented",
            "该节点没有登记生产 Node 调用契约。",
            stage="adapter_dispatch",
            node_id=node_id,
        )

    @staticmethod
    def _record(value: Any) -> CandidateRecord:
        return (
            value
            if isinstance(value, CandidateRecord)
            else CandidateRecord.from_dict(dict(value))
        )

    def _project_output(
        self,
        node_id: str,
        patch: Mapping[str, Any],
        run_store: _LabRunArtifactStore,
    ) -> dict[str, Any]:
        output = dict(patch)
        if node_id == "initialize_run":
            output["reference_artifact_id"] = output.pop("reference_ref")
            config = run_store.descriptor_for("run-config.json")
            if config is not None:
                output["run_config_artifact_id"] = config.artifact_id
            output.pop("image", None)
            output.pop("content_type", None)
        elif node_id == "prepare_context":
            context_pack = ensure_json_object(_json_value(output.pop("context_pack")))
            context_ref = run_store.write_json("lab/context-pack.json", context_pack)
            output["context_pack_artifact_id"] = context_ref.relative_path
            output["context_summary"] = {
                "estimated_tokens": int(context_pack.get("estimated_tokens", 0)),
                "selected_count": len(context_pack.get("selected_memory_ids", [])),
                "dropped_memory_count": int(
                    context_pack.get("dropped_memory_count", 0)
                ),
            }
        elif node_id == "measure_target":
            measurements = output.get("target_measurements")
            output["target_measurements"] = _json_value(measurements)
            descriptor = run_store.descriptor_for("analysis/measurements.json")
            if descriptor is not None:
                output["measurements_artifact"] = descriptor.to_dict()
        elif node_id == "persist_visual_analysis":
            descriptor = run_store.descriptor_for("analysis/visual-analysis.json")
            if descriptor is not None:
                output["visual_analysis_artifact_id"] = descriptor.artifact_id
        elif node_id == "prepare_measurement_seed":
            author = ensure_json_object(_json_value(output.pop("author_result")))
            provenance = ensure_json_object(
                _json_value(output.pop("candidate_provenance"))
            )
            glsl = str(output.pop("glsl"))
            author_ref = run_store.write_json(
                "lab/measurement-seed/author.json", author
            )
            provenance_ref = run_store.write_json(
                "lab/measurement-seed/provenance.json", provenance
            )
            glsl_ref = run_store.write_text(
                "lab/measurement-seed/shader.frag",
                glsl,
                content_type="text/x-glsl; charset=utf-8",
            )
            output.update(
                {
                    "author_artifact_id": author_ref.relative_path,
                    "author_summary": {
                        "author_version": author.get("author_version"),
                        "mode": author.get("mode"),
                        "strategy_summary": author.get("strategy_summary"),
                        "implemented_layers": author.get("implemented_layers", []),
                    },
                    "glsl_artifact_id": glsl_ref.relative_path,
                    "glsl_sha256": glsl_ref.sha256,
                    "glsl_chars": len(glsl),
                    "candidate_provenance_artifact_id": provenance_ref.relative_path,
                }
            )
        elif node_id == "materialize_candidate":
            record = self._record(output["candidate_record"])
            descriptor = run_store.descriptor_for(
                f"candidates/{record.candidate_id}/manifest.json"
            )
            if descriptor is not None:
                output["candidate_manifest_artifact_id"] = descriptor.artifact_id
        elif node_id == "render_and_evaluate":
            compile_result = output.get("compile_result")
            if isinstance(compile_result, Mapping):
                output["compile_result"] = {
                    "success": bool(compile_result.get("success")),
                    "draw_error": compile_result.get("draw_error"),
                    "static_validation": compile_result.get("static_validation"),
                    "vertex_log_chars": len(str(compile_result.get("vertex_log", ""))),
                    "vertex_log_sha256": sha256(
                        str(compile_result.get("vertex_log", "")).encode("utf-8")
                    ).hexdigest(),
                    "fragment_log_chars": len(
                        str(compile_result.get("fragment_log", ""))
                    ),
                    "fragment_log_sha256": sha256(
                        str(compile_result.get("fragment_log", "")).encode("utf-8")
                    ).hexdigest(),
                    "link_log_chars": len(str(compile_result.get("link_log", ""))),
                    "link_log_sha256": sha256(
                        str(compile_result.get("link_log", "")).encode("utf-8")
                    ).hexdigest(),
                }
            output.setdefault("score_breakdown", None)
            record_raw = output.get("candidate_record")
            render_record = self._record(record_raw) if record_raw is not None else None
            if render_record is not None and render_record.render_ref is not None:
                render_descriptor, _data = self._artifacts.read_artifact(
                    run_store.lab_run_id, render_record.render_ref
                )
                output["rendered_image_artifact_id"] = render_descriptor.artifact_id
                output["render_artifact"] = render_descriptor.to_dict()
            if render_record is not None and render_record.metrics_ref is not None:
                metrics_descriptor, _data = self._artifacts.read_artifact(
                    run_store.lab_run_id, render_record.metrics_ref
                )
                output["metrics_artifact"] = metrics_descriptor.to_dict()
            output.pop("rendered_image", None)
            output.pop("rendered_content_type", None)
        elif node_id == "select_current_best":
            selection_ref = output.pop("selection_ref")
            output["selection_artifact_id"] = selection_ref
        elif node_id == "prepare_compile_repair":
            previous = ensure_json_object(
                _json_value(output.pop("previous_author_result"))
            )
            previous_ref = run_store.write_json(
                "lab/compile-repair/author.json", previous
            )
            output["previous_author_artifact_id"] = previous_ref.relative_path
        elif node_id == "load_current_best":
            record = self._record(output["candidate_record"])
            output["author_artifact_id"] = record.author_ref
            output["glsl_artifact_id"] = record.glsl_ref
            output["rendered_image_artifact_id"] = record.render_ref
            output.pop("author_result", None)
            output.pop("glsl", None)
            output.pop("rendered_image", None)
            output.pop("rendered_content_type", None)
        elif node_id == "persist_visual_review":
            record = self._record(output["current_best_record"])
            output["review_artifact_id"] = record.review_ref
            descriptor = run_store.descriptor_for(
                f"candidates/{record.candidate_id}/manifest.json"
            )
            if descriptor is not None:
                output["candidate_manifest_artifact_id"] = descriptor.artifact_id
        elif node_id == "finalize":
            final = dict(output["final_result"])
            final.pop("glsl", None)
            final["glsl_artifact_id"] = final.pop("glsl_ref", None)
            final["render_artifact_id"] = final.pop("render_ref", None)
            final["metrics_artifact_id"] = final.pop("metrics_ref", None)
            final["manifest_artifact_id"] = final.pop("manifest_ref")
            output["final_result"] = final
            output["final_manifest_artifact_id"] = output.pop("final_manifest_ref")
            output.pop("rendered_image", None)
        return ensure_json_object(_json_value(output), path="$.output_patch")

    async def execute(
        self,
        descriptor: NodeDescriptor,
        request: StepExecutionRequest,
        state: Mapping[str, object],
    ) -> NodeExecutionResult:
        """调用生产 Node，并仅把二进制/领域对象投影为 Lab 契约."""
        if descriptor.node_id not in SUPPORTED_NODE_IDS:
            raise NodeLabError(
                "node_adapter_not_implemented",
                "该节点不属于确定性生产 Node allowlist。",
                stage="adapter_dispatch",
                lab_run_id=request.lab_run_id,
                node_id=descriptor.node_id,
            )
        self.preflight(descriptor, request)
        run_store = _LabRunArtifactStore(self._artifacts, request.lab_run_id)
        store = _LabNodeArtifactStore(self._artifacts, request.lab_run_id, run_store)
        execution_usage = {"browser_launch_count": 0}
        try:
            hydrated = self._hydrate(request, state)
            patch = await self._invoke(
                descriptor.node_id,
                hydrated,
                store,
                execution_usage,
            )
            output = self._project_output(descriptor.node_id, patch, run_store)
            if descriptor.node_id == "finalize" and self._resource_cleaner is not None:
                try:
                    await self._resource_cleaner.close(request.lab_run_id)
                except Exception as exc:  # noqa: BLE001 - 清理失败不遮蔽 Node 结果
                    events = list(output.get("events", []))
                    events.append(
                        {
                            "stage": "finalize",
                            "event_type": "resource_close_failed",
                            "payload": {"error_type": type(exc).__name__},
                        }
                    )
                    output["events"] = events
        except NodeLabError:
            raise
        except NodeEvidenceError as exc:
            raise NodeLabError(
                "artifact_integrity_failed",
                "生产 Node 拒绝了不一致的 Artifact 证据。",
                stage="production_node",
                lab_run_id=request.lab_run_id,
                node_id=descriptor.node_id,
            ) from exc
        except Exception as exc:
            if descriptor.node_id == "prepare_context" and bool(
                state.get("memory_strict", False)
            ):
                raise NodeLabError(
                    "memory_unavailable",
                    "Node Lab Context 所需 Memory 不可用。",
                    stage="memory_read",
                    retryable=True,
                    lab_run_id=request.lab_run_id,
                    node_id=descriptor.node_id,
                ) from exc
            if isinstance(exc, (KeyError, TypeError, ValueError, RuntimeError)):
                raise NodeLabError(
                    "input_contract_invalid",
                    "节点输入无法通过生产 Node 契约校验。",
                    stage="production_node",
                    lab_run_id=request.lab_run_id,
                    node_id=descriptor.node_id,
                    details={"error_type": type(exc).__name__},
                ) from exc
            raise

        outcome = "success"
        next_action = None
        if descriptor.node_id in {"decide_after_render", "decide_after_selection"}:
            next_action = str(output["next_action"])
            if next_action == "finalize":
                outcome = "stopped"
        elif descriptor.node_id == "select_current_best":
            if not bool(output["selection_decision"]["accepted"]):
                outcome = "rejected"
        elif descriptor.node_id == "render_and_evaluate":
            if output.get("render_status") == "compile_failed":
                outcome = "rejected"
            elif output.get("render_status") != "success":
                outcome = "stopped"
        elif descriptor.node_id == "finalize":
            if not bool(output["final_result"]["success"]):
                outcome = "stopped"
        elif descriptor.node_id == "promote_validated_strategy":
            if output.get("memory_preview") is None:
                outcome = "stopped"

        implementation = f"agent.app.nodes.{descriptor.node_id}"
        if descriptor.node_id in {"decide_after_render", "decide_after_selection"}:
            implementation = (
                f"agent.app.graphs.png_to_shader_v1_routing.{descriptor.node_id}"
            )
        usage: dict[str, Any] = {
            "model_call_count": 0,
            "browser_launch_count": execution_usage["browser_launch_count"],
        }
        provenance: dict[str, Any] = {
            "execution_source": "production_node",
            "implementation": implementation,
        }
        if descriptor.node_id == "render_and_evaluate":
            provenance["renderer_lifecycle"] = "cold_per_node_step"
        if descriptor.node_id == "promote_validated_strategy":
            provenance["memory_write"] = False
            usage["memory_write_count"] = 0
        return NodeExecutionResult(
            outcome=outcome,  # type: ignore[arg-type]
            output_patch=output,
            artifacts=run_store.created,
            provenance=provenance,
            usage=usage,
            next_action=next_action,
        )
