"""PNG 转无贴图 Shader V1 的确定性运行、候选和 Artifact 节点."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, replace
from hashlib import sha256
from typing import Any, Literal, Protocol
from uuid import uuid4

from agent.app.contracts.png_to_shader_v1 import (
    CandidateProvenance,
    CandidateRecordInput,
    ShaderAuthorResult,
    VisualAnalysis,
)
from shaderforge.analysis import (
    RegionOfInterest,
    TargetMeasurements,
    measure_target,
    normalize_target_png,
)
from shaderforge.contracts import (
    DEFAULT_ACCEPTANCE_POLICY,
    WEBGL1_STATIC_NO_TEXTURE_V1,
    AcceptancePolicy,
    BudgetPolicy,
    QualityPreset,
    StopReason,
    budget_for_preset,
)
from shaderforge.evaluation import (
    CandidateRecord,
    ScoreBreakdownV1,
    select_current_best,
)
from shaderforge.generation import build_measurement_affine_seed
from shaderforge.rendering import (
    CompileResult,
    RendererUnavailableError,
    RenderResult,
)
from shaderforge.store import LocalArtifactStore, RunArtifactStore
from shaderforge.validation import (
    ShaderRepairResult,
    ValidationResult,
    repair_constant_reversed_smoothsteps,
    validate_shader,
)

Clock = Callable[[], float]
RendererFactory = Callable[[int], "ShaderRenderer"]
RunNode = Callable[[Mapping[str, Any]], Awaitable[dict[str, Any]]]
logger = logging.getLogger("agent.png_to_shader")
MAX_FINALIZE_RESERVE_SECONDS = 30.0
FINALIZE_RESERVE_RATIO = 0.10
RENDERER_CLOSE_TIMEOUT_SECONDS = 3.0


class NodeEvidenceError(RuntimeError):
    """生产 Node 发现输入 Artifact/摘要之间的证据绑定不一致."""


class ShaderRenderer(Protocol):
    """M3 编排依赖的最小异步渲染接口."""

    async def render(
        self, fragment_source: str, width: int, height: int
    ) -> RenderResult:
        """编译并渲染一个 Fragment Shader."""
        ...

    async def close(self) -> None:
        """释放当前 run 的渲染资源."""
        ...


class RenderEvaluator(Protocol):
    """M3 编排依赖的确定性、无外部副作用评分接口."""

    def __call__(
        self,
        reference_image: bytes,
        candidate_image: bytes,
        *,
        measurements: TargetMeasurements,
    ) -> ScoreBreakdownV1:
        """返回候选相对参考图的评分向量."""
        ...


class RunRendererRegistry:
    """按 project/run 隔离并复用 Renderer，finalize 时释放."""

    def __init__(self, factory: RendererFactory) -> None:
        """保存工厂并初始化空的 run 级资源表."""
        self._factory = factory
        self._renderers: dict[tuple[str, str], ShaderRenderer] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def render(
        self,
        key: tuple[str, str],
        *,
        replay_on_worker_failure: int,
        fragment_source: str,
        width: int,
        height: int,
    ) -> RenderResult:
        """在当前 run 的串行锁内复用 Renderer."""
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            renderer = self._renderers.get(key)
            if renderer is None:
                renderer = self._factory(replay_on_worker_failure)
                self._renderers[key] = renderer
            return await renderer.render(fragment_source, width, height)

    async def close(self, key: tuple[str, str]) -> None:
        """幂等移除并关闭一个 run 的 Renderer."""
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            renderer = self._renderers.pop(key, None)
            if renderer is not None:
                await renderer.close()
        self._locks.pop(key, None)


def _run_key(state: Mapping[str, Any]) -> tuple[str, str]:
    return str(state["project_id"]), str(state["run_id"])


def _run_store(
    artifact_store: LocalArtifactStore,
    state: Mapping[str, Any],
) -> RunArtifactStore:
    return artifact_store.start_run(str(state["project_id"]), str(state["run_id"]))


def _budget(state: Mapping[str, Any]) -> BudgetPolicy:
    value = state["budget_policy"]
    if isinstance(value, BudgetPolicy):
        return value
    return BudgetPolicy(**dict(value))


def _acceptance(state: Mapping[str, Any]) -> AcceptancePolicy:
    value = state["acceptance_policy"]
    if isinstance(value, AcceptancePolicy):
        return value
    return AcceptancePolicy(**dict(value))


def _record(value: Any) -> CandidateRecord:
    if isinstance(value, CandidateRecord):
        return value
    return CandidateRecord.from_dict(dict(value))


def _replace_record(
    records: tuple[Any, ...],
    replacement: CandidateRecord,
) -> tuple[CandidateRecord, ...]:
    normalized = tuple(_record(item) for item in records)
    return tuple(
        replacement if item.candidate_id == replacement.candidate_id else item
        for item in normalized
    )


def _read_json(store: RunArtifactStore, relative_path: str) -> dict[str, Any]:
    value = json.loads(store.read_bytes(relative_path))
    if not isinstance(value, dict):
        raise ValueError("Artifact JSON 根节点必须是 object。")
    return value


def _candidate_manifest_path(candidate_id: str) -> str:
    return f"candidates/{candidate_id}/manifest.json"


def _write_candidate_manifest(
    store: RunArtifactStore,
    record: CandidateRecord,
) -> None:
    store.write_json(_candidate_manifest_path(record.candidate_id), record.to_dict())


def _wall_remaining(state: Mapping[str, Any], clock: Clock) -> float:
    return _budget(state).max_wall_time_seconds - (clock() - float(state["started_at"]))


def _elapsed_seconds(state: Mapping[str, Any], clock: Clock) -> float:
    return max(0.0, clock() - float(state["started_at"]))


def _finalize_reserve_seconds(state: Mapping[str, Any]) -> float:
    budget = _budget(state)
    return min(
        MAX_FINALIZE_RESERVE_SECONDS,
        budget.max_wall_time_seconds * FINALIZE_RESERVE_RATIO,
    )


def _work_seconds_before_finalize(
    state: Mapping[str, Any],
    clock: Clock,
) -> float:
    return _wall_remaining(state, clock) - _finalize_reserve_seconds(state)


def _evaluation_measurements(
    state: Mapping[str, Any],
    measurements: TargetMeasurements,
) -> TargetMeasurements:
    """把模型确认的语义 ROI 合并进确定性评分目标.

    基础测量 ROI 始终保留；VisualAnalysis 只能追加不同 id 的区域，不能覆盖
    `subject`、`background_border` 等确定性证据。这样 Critic 和 Selector 能看到
    高光、阴影、颜色与保护区残差，同时仍以同一张规范化参考图为事实来源。
    """
    raw_analysis = state.get("visual_analysis")
    if raw_analysis is None:
        return measurements
    analysis = (
        raw_analysis
        if isinstance(raw_analysis, VisualAnalysis)
        else VisualAnalysis.model_validate(raw_analysis)
    )
    region_ids = {region.region_id for region in measurements.roi_candidates}
    semantic_regions: list[RegionOfInterest] = []
    for region in analysis.regions_of_interest:
        if region.region_id in region_ids:
            continue
        region_ids.add(region.region_id)
        semantic_regions.append(
            RegionOfInterest(
                region_id=region.region_id,
                bbox_uv=region.bbox_uv,
                purpose=region.purpose,
                confidence=region.confidence,
            )
        )
    if not semantic_regions:
        return measurements
    return replace(
        measurements,
        roi_candidates=(*measurements.roi_candidates, *semantic_regions),
    )


def _validation_diagnostics(validation: ValidationResult) -> dict[str, Any]:
    violations = [
        {
            "code": item.code,
            "severity": item.severity,
            "line": item.line,
        }
        for item in validation.violations
    ]
    return {
        "violation_codes": list(dict.fromkeys(item["code"] for item in violations)),
        "violations": violations,
    }


def _persist_deterministic_shader_repair(
    store: RunArtifactStore,
    state: Mapping[str, Any],
    record: CandidateRecord,
    repair: ShaderRepairResult,
) -> tuple[CandidateRecord, dict[str, Any], dict[str, Any], dict[str, Any]]:
    """写入新的修复产物后切换候选绑定，并返回安全审计摘要."""
    original_sha256 = record.glsl_sha256
    repair_prefix = f"candidates/{record.candidate_id}/deterministic-repair"
    glsl_ref = store.write_text(
        f"{repair_prefix}/shader.frag",
        repair.source,
        content_type="text/x-glsl; charset=utf-8",
    )
    author = dict(state["author_result"])
    author["glsl"] = repair.source
    provenance = dict(state["candidate_provenance"])
    provenance["glsl_sha256"] = glsl_ref.sha256
    author_ref = store.write_json(f"{repair_prefix}/author.json", author)
    provenance_ref = store.write_json(f"{repair_prefix}/provenance.json", provenance)
    repaired_record = replace(
        record,
        glsl_sha256=glsl_ref.sha256,
        glsl_ref=glsl_ref.relative_path,
        author_ref=author_ref.relative_path,
        provenance_ref=provenance_ref.relative_path,
    )
    _write_candidate_manifest(store, repaired_record)
    audit = {
        **repair.safe_audit_dict(),
        "before_glsl_sha256": original_sha256,
        "after_glsl_sha256": glsl_ref.sha256,
        "repaired_glsl_ref": glsl_ref.relative_path,
    }
    return repaired_record, author, provenance, audit


def make_initialize_png_to_shader_v1_node(
    artifact_store: LocalArtifactStore,
    *,
    clock: Clock,
) -> RunNode:
    """创建规范化输入并冻结本次策略的初始化节点."""

    async def initialize(state: Mapping[str, Any]) -> dict[str, Any]:
        project_id = str(state.get("project_id", "")).strip()
        if not project_id:
            raise ValueError("project_id 不能为空。")
        image = state.get("image")
        if not isinstance(image, bytes) or not image:
            raise ValueError("image 必须是非空 bytes。")

        preset_value = str(state.get("quality_preset", QualityPreset.BALANCED.value))
        preset = QualityPreset(preset_value)
        raw_budget = state.get("budget_policy")
        budget = (
            raw_budget
            if isinstance(raw_budget, BudgetPolicy)
            else BudgetPolicy(**dict(raw_budget))
            if raw_budget is not None
            else budget_for_preset(preset)
        )
        high_ceiling = budget_for_preset(QualityPreset.HIGH)
        if (
            budget.max_visual_refinements > high_ceiling.max_visual_refinements
            or budget.max_compile_repairs > high_ceiling.max_compile_repairs
            or budget.max_model_calls > high_ceiling.max_model_calls
            or budget.max_wall_time_seconds > high_ceiling.max_wall_time_seconds
            or budget.max_shader_chars > high_ceiling.max_shader_chars
            or budget.renderer_replay_on_crash > high_ceiling.renderer_replay_on_crash
        ):
            raise ValueError("自定义 budget_policy 不得超过 V1 high 档硬上限。")
        raw_acceptance = state.get("acceptance_policy")
        acceptance = (
            raw_acceptance
            if isinstance(raw_acceptance, AcceptancePolicy)
            else AcceptancePolicy(**dict(raw_acceptance))
            if raw_acceptance is not None
            else DEFAULT_ACCEPTANCE_POLICY
        )
        run_id = str(state.get("run_id") or uuid4().hex)
        started_at = clock()
        reference = normalize_target_png(
            image,
            max_long_side=WEBGL1_STATIC_NO_TEXTURE_V1.max_long_side,
        )
        store = artifact_store.register_run(project_id, run_id)
        store.write_bytes("input/source.bin", image)
        reference_ref = store.write_bytes(
            "input/reference.png",
            reference,
            content_type="image/png",
        )
        store.write_json(
            "run-config.json",
            {
                "schema_version": 1,
                "project_id": project_id,
                "run_id": run_id,
                "quality_preset": preset.value,
                "render_contract": WEBGL1_STATIC_NO_TEXTURE_V1.to_dict(),
                "budget_policy": asdict(budget),
                "acceptance_policy": asdict(acceptance),
            },
        )
        logger.info(
            "shader.pipeline.initialized run_id=%s project_id=%s "
            "quality_preset=%s max_wall_time_seconds=%s max_model_calls=%s",
            run_id,
            project_id,
            preset.value,
            budget.max_wall_time_seconds,
            budget.max_model_calls,
        )
        return {
            "project_id": project_id,
            "run_id": run_id,
            "phase": "initialized",
            "quality_preset": preset.value,
            "iteration": 0,
            "current_candidate_id": "",
            "current_best_id": "",
            "current_best_glsl_sha256": "",
            "current_best_total_loss": 1.0,
            "current_best_score_summary": {},
            "compile_repair_count": 0,
            "visual_refinement_count": 0,
            "no_improvement_count": 0,
            "model_call_count": 0,
            "candidate_sequence": 0,
            "measurement_seed_attempted": False,
            "stop_reason": "",
            "cancelled": bool(state.get("cancelled", False)),
            "image": reference,
            "content_type": "image/png",
            "instruction": str(state.get("instruction", "")).strip(),
            "render_contract": WEBGL1_STATIC_NO_TEXTURE_V1.to_dict(),
            "budget_policy": asdict(budget),
            "acceptance_policy": asdict(acceptance),
            "started_at": started_at,
            "reference_ref": reference_ref.relative_path,
            "candidate_records": (),
            "current_best_record": None,
            "model_calls": (),
            "events": (
                {
                    "stage": "initialize",
                    "event_type": "run_initialized",
                    "payload": {
                        "run_id": run_id,
                        "quality_preset": preset.value,
                        "reference_sha256": reference_ref.sha256,
                    },
                },
            ),
            "logs": (),
            "memory_status": str(state.get("memory_status", "ephemeral")),
        }

    return initialize


def make_measure_target_node(artifact_store: LocalArtifactStore) -> RunNode:
    """创建确定性测量参考图并持久化结果的节点."""

    async def measure(state: Mapping[str, Any]) -> dict[str, Any]:
        measurements = measure_target(state["image"])
        store = _run_store(artifact_store, state)
        store.write_json("analysis/measurements.json", measurements)
        return {
            "phase": "measured",
            "target_measurements": measurements,
            "events": (
                *state.get("events", ()),
                {
                    "stage": "measure_target",
                    "event_type": "target_measured",
                    "payload": {
                        "width": measurements.analysis_width,
                        "height": measurements.analysis_height,
                        "image_sha256": measurements.image_sha256,
                    },
                },
            ),
        }

    return measure


def make_persist_visual_analysis_node(artifact_store: LocalArtifactStore) -> RunNode:
    """创建只持久化已通过 M2 Parser 的 VisualAnalysis 节点."""

    async def persist(state: Mapping[str, Any]) -> dict[str, Any]:
        store = _run_store(artifact_store, state)
        artifact = store.write_json(
            "analysis/visual-analysis.json",
            state["visual_analysis"],
        )
        return {
            "phase": "analyzed",
            "events": (
                *state.get("events", ()),
                {
                    "stage": "visual_analysis",
                    "event_type": "analysis_persisted",
                    "payload": {"artifact_ref": artifact.relative_path},
                },
            ),
        }

    return persist


def make_materialize_candidate_node(artifact_store: LocalArtifactStore) -> RunNode:
    """创建把 Author 输出冻结为新候选及 provenance 的节点."""

    async def materialize(state: Mapping[str, Any]) -> dict[str, Any]:
        sequence = int(state.get("candidate_sequence", 0)) + 1
        candidate_id = f"candidate-{sequence:04d}"
        author = dict(state["author_result"])
        provenance = dict(state["candidate_provenance"])
        glsl = str(state["glsl"])
        glsl_sha256 = sha256(glsl.encode("utf-8")).hexdigest()
        raw_origin = str(state.get("candidate_origin", "model"))
        origin: Literal["model", "deterministic"]
        if raw_origin == "model":
            origin = "model"
        elif raw_origin == "deterministic":
            origin = "deterministic"
        else:
            raise ValueError("candidate_origin 必须是 model 或 deterministic。")
        generator_version = state.get("candidate_generator_version")
        if origin == "deterministic" and not generator_version:
            raise ValueError("确定性候选必须绑定 generator_version。")
        if origin == "deterministic":
            expected_model_ref = f"deterministic:{generator_version}"
            if (
                author.get("author_version") != generator_version
                or author.get("mode") != "measurement_seed"
                or author.get("base_candidate_id") is not None
                or author.get("glsl") != glsl
                or author.get("changed_problem_domain") != "initial_build"
                or provenance.get("role") != "deterministic_generator"
                or provenance.get("origin") != "deterministic"
                or provenance.get("generator_version") != generator_version
                or provenance.get("prompt_version") != generator_version
                or provenance.get("model_ref") != expected_model_ref
                or provenance.get("requested_model_ref") != expected_model_ref
                or provenance.get("glsl_sha256") != glsl_sha256
            ):
                raise NodeEvidenceError(
                    "确定性 Author、provenance 与 GLSL 证据绑定不一致。"
                )
            mode = "measurement_seed"
        else:
            parsed_author = ShaderAuthorResult.model_validate(author)
            parsed_provenance = CandidateProvenance.model_validate(provenance)
            expected_author_versions = {
                "initial": "shader_author_initial_v1_1",
                "compile_repair": "shader_author_compile_repair_v1_1",
                "visual_refine": "shader_author_visual_refine_v1",
            }
            mode_value = parsed_author.mode.value
            expected_author_version = expected_author_versions[mode_value]
            if (
                generator_version is not None
                or parsed_author.author_version != expected_author_version
                or parsed_author.glsl != glsl
                or parsed_provenance.glsl_sha256 != glsl_sha256
                or parsed_author.mode != parsed_provenance.mode
                or parsed_provenance.prompt_version != expected_author_version
                or (
                    state.get("author_model") is not None
                    and str(state["author_model"]) != parsed_provenance.model_ref
                )
            ):
                raise NodeEvidenceError("Author、provenance 与 GLSL 证据绑定不一致。")
            if mode_value == "initial" and (
                parsed_author.base_candidate_id is not None
                or parsed_author.changed_problem_domain != "initial_build"
                or parsed_author.changed_parameters
                or parsed_author.protected_regions
            ):
                raise NodeEvidenceError("initial Author 的根候选证据绑定不一致。")
            if mode_value == "compile_repair" and (
                parsed_author.changed_problem_domain != "runtime_compile"
            ):
                raise NodeEvidenceError("compile repair Author 的修改域不合法。")
            if mode_value == "visual_refine":
                base_candidate_id = parsed_author.base_candidate_id
                expected_base = state.get("current_best_id")
                if not base_candidate_id or (
                    expected_base is not None
                    and str(expected_base)
                    and base_candidate_id != str(expected_base)
                ):
                    raise NodeEvidenceError(
                        "visual refine Author 未绑定 current_best。"
                    )
            mode = mode_value
        if origin == "deterministic" or mode == "initial":
            parent_id = None
        elif mode == "visual_refine":
            parent_id = str(author["base_candidate_id"])
        else:
            parent_id = str(state["current_candidate_id"])

        store = _run_store(artifact_store, state)
        prefix = f"candidates/{candidate_id}"
        glsl_ref = store.write_text(
            f"{prefix}/shader.frag",
            glsl,
            content_type="text/x-glsl; charset=utf-8",
        )
        author_ref = store.write_json(f"{prefix}/author.json", author)
        provenance_ref = store.write_json(
            f"{prefix}/provenance.json",
            provenance,
        )
        record = CandidateRecord(
            candidate_id=candidate_id,
            parent_candidate_id=parent_id,
            glsl_sha256=glsl_ref.sha256,
            glsl_ref=glsl_ref.relative_path,
            author_ref=author_ref.relative_path,
            provenance_ref=provenance_ref.relative_path,
            compile_ref=None,
            render_ref=None,
            render_sha256=None,
            metrics_ref=None,
            review_ref=None,
            iteration=int(state.get("visual_refinement_count", 0)),
            changed_problem_domain=str(author["changed_problem_domain"]),
            prompt_version=str(provenance["prompt_version"]),
            model_ref=str(provenance["model_ref"]),
            score_summary=None,
            hard_constraints_passed=False,
            origin=origin,
            generator_version=(
                str(generator_version) if generator_version is not None else None
            ),
        )
        _write_candidate_manifest(store, record)
        return {
            "phase": "candidate_materialized",
            "candidate_sequence": sequence,
            "current_candidate_id": candidate_id,
            "candidate_record": record,
            "candidate_records": (*state.get("candidate_records", ()), record),
            "render_status": "pending",
            "events": (
                *state.get("events", ()),
                {
                    "stage": "candidate",
                    "event_type": "candidate_created",
                    "payload": {
                        "candidate_id": candidate_id,
                        "parent_candidate_id": parent_id,
                        "mode": mode,
                        "origin": origin,
                        "generator_version": record.generator_version,
                        "glsl_sha256": record.glsl_sha256,
                    },
                },
            ),
        }

    return materialize


def make_prepare_measurement_seed_node() -> RunNode:
    """创建与 model initial 并列的确定性 measurement affine 根候选."""

    async def prepare(state: Mapping[str, Any]) -> dict[str, Any]:
        if bool(state.get("measurement_seed_attempted", False)):
            raise RuntimeError("measurement seed 每个 run 只能准备一次。")
        reference = state.get("image")
        measurements = state.get("target_measurements")
        if not isinstance(reference, bytes) or not reference:
            raise TypeError("measurement seed 需要规范化 reference bytes。")
        if not isinstance(measurements, TargetMeasurements):
            raise TypeError("measurement seed 需要 TargetMeasurements。")
        if measurements.image_sha256 != sha256(reference).hexdigest():
            raise NodeEvidenceError(
                "TargetMeasurements 与规范化 reference 证据绑定不一致。"
            )
        seed = build_measurement_affine_seed(reference, measurements)
        generator_version = seed.provenance.generator_version
        model_ref = f"deterministic:{generator_version}"
        source = {
            "author_version": generator_version,
            "mode": "measurement_seed",
            "base_candidate_id": None,
            "glsl": seed.glsl,
            "strategy_summary": (
                "用主体 bbox 椭圆和前景 RGB affine plane 构造紧凑无贴图根候选。"
            ),
            "implemented_layers": ["background", "measurement_affine_subject"],
            "parameter_manifest": [],
            "changed_problem_domain": "initial_build",
            "changed_parameters": [],
            "protected_regions": [],
            "expected_metric_changes": ["提供可由 Selector 独立验收的测量基线。"],
            "known_limitations": ["V1 seed 只表达单主体椭圆和一阶连续颜色场。"],
        }
        provenance = {
            **seed.provenance.to_dict(),
            "role": "deterministic_generator",
            "origin": "deterministic",
            "model_ref": model_ref,
            "requested_model_ref": model_ref,
            "model_identity_source": "deterministic_generator",
            # CandidateRecord v1 保留 prompt_version 字段；确定性候选在此字段中
            # 记录 generator version，另由 origin/generator_version 消除歧义。
            "prompt_version": generator_version,
        }
        return {
            "phase": "measurement_seed_prepared",
            "measurement_seed_attempted": True,
            "author_result": source,
            "glsl": seed.glsl,
            "author_model": model_ref,
            "candidate_provenance": provenance,
            "candidate_origin": "deterministic",
            "candidate_generator_version": generator_version,
            "events": (
                *state.get("events", ()),
                {
                    "stage": "candidate",
                    "event_type": "measurement_seed_prepared",
                    "payload": {
                        "generator_version": generator_version,
                        "strategy": seed.provenance.strategy,
                        "glsl_sha256": seed.provenance.glsl_sha256,
                        "fit_pixel_count": seed.provenance.fit_pixel_count,
                        "fit_rmse": seed.provenance.fit_rmse,
                        "fallback_reason": seed.provenance.fallback_reason,
                    },
                },
            ),
        }

    return prepare


def make_render_and_evaluate_node(
    artifact_store: LocalArtifactStore,
    renderer_registry: RunRendererRegistry,
    evaluator: RenderEvaluator,
    *,
    clock: Clock,
) -> RunNode:
    """创建静态校验、真实 WebGL1 渲染、评分和证据绑定节点."""

    async def render_and_evaluate(state: Mapping[str, Any]) -> dict[str, Any]:
        budget = _budget(state)
        record = _record(state["candidate_record"])
        store = _run_store(artifact_store, state)
        prefix = f"candidates/{record.candidate_id}"
        glsl = str(state["glsl"])
        try:
            persisted_glsl = store.read_bytes(record.glsl_ref).decode("utf-8")
        except (FileNotFoundError, UnicodeDecodeError) as exc:
            raise NodeEvidenceError(
                "CandidateRecord 引用的 GLSL 证据不可读取。"
            ) from exc
        if (
            persisted_glsl != glsl
            or sha256(glsl.encode("utf-8")).hexdigest() != record.glsl_sha256
        ):
            raise NodeEvidenceError("CandidateRecord 与 GLSL 证据绑定不一致。")
        run_id, project_id = str(state["run_id"]), str(state["project_id"])
        logger.info(
            "shader.pipeline.render.started run_id=%s project_id=%s "
            "candidate_id=%s glsl_chars=%s",
            run_id,
            project_id,
            record.candidate_id,
            len(glsl),
        )
        validation = validate_shader(
            glsl,
            max_shader_chars=budget.max_shader_chars,
        )
        events = tuple(state.get("events", ()))
        repair_update: dict[str, Any] = {}
        blocking_codes = {item.code for item in validation.errors}
        if blocking_codes == {"reversed_smoothstep_edges"}:
            repair = repair_constant_reversed_smoothsteps(glsl)
            if repair is not None:
                repaired_validation = validate_shader(
                    repair.source,
                    max_shader_chars=budget.max_shader_chars,
                )
                if repaired_validation.valid:
                    record, author, provenance, repair_audit = (
                        _persist_deterministic_shader_repair(
                            store,
                            state,
                            record,
                            repair,
                        )
                    )
                    glsl = repair.source
                    validation = repaired_validation
                    repair_update = {
                        "glsl": glsl,
                        "author_result": author,
                        "candidate_provenance": provenance,
                        "candidate_record": record,
                        "candidate_records": _replace_record(
                            tuple(state.get("candidate_records", ())), record
                        ),
                        "logs": (
                            *state.get("logs", ()),
                            {
                                "level": "warning",
                                "source": "shaderforge.validation",
                                "message": "常量倒序 smoothstep 已执行确定性修复并重验通过",
                                "context": repair_audit,
                            },
                        ),
                    }
                    events = (
                        *events,
                        {
                            "stage": "render",
                            "event_type": "shader_deterministically_repaired",
                            "payload": {
                                "candidate_id": record.candidate_id,
                                **repair_audit,
                                "elapsed_seconds": round(
                                    _elapsed_seconds(state, clock), 3
                                ),
                            },
                        },
                    )
                    logger.warning(
                        "shader.pipeline.local_repair run_id=%s project_id=%s "
                        "stage=static_validation candidate_id=%s strategy=%s "
                        "replacement_count=%s repaired_lines=%s",
                        run_id,
                        project_id,
                        record.candidate_id,
                        repair.strategy,
                        repair.replacement_count,
                        ",".join(str(line) for line in repair.repaired_lines),
                    )
        if not validation.valid:
            logger.warning(
                "shader.pipeline.render.failed run_id=%s project_id=%s "
                "candidate_id=%s failure_stage=static_validation violation_codes=%s",
                run_id,
                project_id,
                record.candidate_id,
                ",".join(item.code for item in validation.violations),
            )
            compile_result = CompileResult(
                success=False,
                vertex_log="",
                fragment_log="",
                link_log="",
                draw_error="static_validation_failed",
                static_validation=validation,
            )
            compile_ref = store.write_json(
                f"{prefix}/compile.json",
                compile_result,
            )
            failed = replace(record, compile_ref=compile_ref.relative_path)
            _write_candidate_manifest(store, failed)
            return {
                "phase": "compile_failed",
                "candidate_record": failed,
                "candidate_records": _replace_record(
                    tuple(state.get("candidate_records", ())), failed
                ),
                "static_validation": validation.to_dict(),
                "compile_result": compile_result.to_dict(),
                "render_status": "compile_failed",
                "events": (
                    *events,
                    {
                        "stage": "render",
                        "event_type": "compile_failed",
                        "payload": {
                            "candidate_id": record.candidate_id,
                            "failure_stage": "static_validation",
                            **_validation_diagnostics(validation),
                            "elapsed_seconds": round(_elapsed_seconds(state, clock), 3),
                        },
                    },
                ),
            }

        remaining_wall = _wall_remaining(state, clock)
        finalize_reserve = _finalize_reserve_seconds(state)
        renderer_timeout = remaining_wall - finalize_reserve
        if renderer_timeout <= 0.0:
            return {
                **repair_update,
                "phase": "render_skipped",
                "render_status": "wall_time_exhausted",
                "stop_reason": StopReason.WALL_TIME_EXHAUSTED.value,
                "events": (
                    *events,
                    {
                        "stage": "render",
                        "event_type": "renderer_skipped",
                        "payload": {
                            "candidate_id": record.candidate_id,
                            "reason": StopReason.WALL_TIME_EXHAUSTED.value,
                            "remaining_wall_seconds": round(
                                max(0.0, remaining_wall), 3
                            ),
                            "reserved_wall_seconds": round(finalize_reserve, 3),
                            "elapsed_seconds": round(_elapsed_seconds(state, clock), 3),
                        },
                    },
                ),
            }
        measurements = state["target_measurements"]
        if not isinstance(measurements, TargetMeasurements):
            raise TypeError("target_measurements 必须是 TargetMeasurements。")
        evaluation_measurements = _evaluation_measurements(state, measurements)
        try:
            render = await asyncio.wait_for(
                renderer_registry.render(
                    _run_key(state),
                    replay_on_worker_failure=budget.renderer_replay_on_crash,
                    fragment_source=glsl,
                    width=measurements.analysis_width,
                    height=measurements.analysis_height,
                ),
                timeout=renderer_timeout,
            )
        except TimeoutError:
            logger.error(
                "shader.pipeline.render.failed run_id=%s project_id=%s "
                "candidate_id=%s failure_stage=renderer error_type=TimeoutError",
                run_id,
                project_id,
                record.candidate_id,
            )
            return {
                **repair_update,
                "phase": "render_failed",
                "render_status": "wall_time_exhausted",
                "stop_reason": StopReason.WALL_TIME_EXHAUSTED.value,
                "events": (
                    *events,
                    {
                        "stage": "render",
                        "event_type": "renderer_failed",
                        "payload": {
                            "candidate_id": record.candidate_id,
                            "error_type": "TimeoutError",
                            "timeout_seconds": round(renderer_timeout, 3),
                            "reserved_wall_seconds": round(finalize_reserve, 3),
                            "elapsed_seconds": round(_elapsed_seconds(state, clock), 3),
                        },
                    },
                ),
            }
        except RendererUnavailableError as exc:
            logger.error(
                "shader.pipeline.render.failed run_id=%s project_id=%s "
                "candidate_id=%s failure_stage=renderer error_type=%s",
                run_id,
                project_id,
                record.candidate_id,
                type(exc).__name__,
            )
            return {
                **repair_update,
                "phase": "render_failed",
                "render_status": "renderer_unavailable",
                "stop_reason": StopReason.RENDERER_UNAVAILABLE.value,
                "events": (
                    *events,
                    {
                        "stage": "render",
                        "event_type": "renderer_failed",
                        "payload": {
                            "candidate_id": record.candidate_id,
                            "error_type": type(exc).__name__,
                            "elapsed_seconds": round(_elapsed_seconds(state, clock), 3),
                        },
                    },
                ),
            }
        except Exception as exc:
            logger.error(
                "shader.pipeline.render.failed run_id=%s project_id=%s "
                "candidate_id=%s failure_stage=renderer error_type=%s",
                run_id,
                project_id,
                record.candidate_id,
                type(exc).__name__,
            )
            return {
                **repair_update,
                "phase": "render_failed",
                "render_status": "renderer_unavailable",
                "stop_reason": StopReason.RENDERER_UNAVAILABLE.value,
                "events": (
                    *events,
                    {
                        "stage": "render",
                        "event_type": "renderer_failed",
                        "payload": {
                            "candidate_id": record.candidate_id,
                            "error_type": type(exc).__name__,
                            "elapsed_seconds": round(_elapsed_seconds(state, clock), 3),
                        },
                    },
                ),
            }

        compile_ref = store.write_json(f"{prefix}/compile.json", render.compile)
        if (
            not render.success
            or not render.compile.success
            or render.image_bytes is None
        ):
            logger.warning(
                "shader.pipeline.render.failed run_id=%s project_id=%s "
                "candidate_id=%s failure_stage=webgl_compile draw_error=%s",
                run_id,
                project_id,
                record.candidate_id,
                render.compile.draw_error or "none",
            )
            failed = replace(record, compile_ref=compile_ref.relative_path)
            _write_candidate_manifest(store, failed)
            return {
                **repair_update,
                "phase": "compile_failed",
                "candidate_record": failed,
                "candidate_records": _replace_record(
                    tuple(state.get("candidate_records", ())), failed
                ),
                "static_validation": render.compile.static_validation.to_dict(),
                "compile_result": render.compile.to_dict(),
                "render_status": "compile_failed",
                "events": (
                    *events,
                    {
                        "stage": "render",
                        "event_type": "compile_failed",
                        "payload": {
                            "candidate_id": record.candidate_id,
                            "failure_stage": "webgl_compile",
                            **_validation_diagnostics(render.compile.static_validation),
                            "draw_error": render.compile.draw_error,
                            # WebGL 编译器日志可能回显 Shader 源码片段。原文只保留
                            # 在私有 compile Artifact；过程事件仅写长度与摘要，避免
                            # 经 agent_events.payload 进入数据库和普通日志系统。
                            "fragment_log_chars": len(render.compile.fragment_log),
                            "fragment_log_sha256": sha256(
                                render.compile.fragment_log.encode("utf-8")
                            ).hexdigest(),
                            "link_log_chars": len(render.compile.link_log),
                            "link_log_sha256": sha256(
                                render.compile.link_log.encode("utf-8")
                            ).hexdigest(),
                            "elapsed_seconds": round(_elapsed_seconds(state, clock), 3),
                        },
                    },
                ),
            }

        render_ref = store.write_bytes(
            f"{prefix}/render.png",
            render.image_bytes,
            content_type="image/png",
        )
        rendered_record = replace(
            record,
            compile_ref=compile_ref.relative_path,
            render_ref=render_ref.relative_path,
            render_sha256=render_ref.sha256,
            hard_constraints_passed=True,
        )
        _write_candidate_manifest(store, rendered_record)
        evaluation_timeout = _work_seconds_before_finalize(state, clock)
        evaluation_started_at = clock()
        try:
            if evaluation_timeout <= 0.0:
                raise TimeoutError("evaluation deadline unavailable")
            score = await asyncio.wait_for(
                asyncio.to_thread(
                    evaluator,
                    state["image"],
                    render.image_bytes,
                    measurements=evaluation_measurements,
                ),
                timeout=evaluation_timeout,
            )
        except TimeoutError:
            evaluation_elapsed = max(0.0, clock() - evaluation_started_at)
            logger.error(
                "shader.pipeline.evaluate.failed run_id=%s project_id=%s "
                "candidate_id=%s error_type=TimeoutError timeout_seconds=%.2f",
                run_id,
                project_id,
                record.candidate_id,
                max(0.0, evaluation_timeout),
            )
            return {
                **repair_update,
                "phase": "evaluation_failed",
                "candidate_record": rendered_record,
                "candidate_records": _replace_record(
                    tuple(state.get("candidate_records", ())), rendered_record
                ),
                "static_validation": render.compile.static_validation.to_dict(),
                "compile_result": render.compile.to_dict(),
                "render_status": "evaluation_failed",
                "rendered_image": render.image_bytes,
                "rendered_content_type": "image/png",
                "stop_reason": StopReason.COMPLETED_WITH_BEST_EFFORT.value,
                "events": (
                    *events,
                    {
                        "stage": "evaluate",
                        "event_type": "evaluation_failed",
                        "payload": {
                            "candidate_id": record.candidate_id,
                            "failure_stage": "evaluation",
                            "error_type": "TimeoutError",
                            "timeout_source": "finalize_reserve",
                            "timeout_seconds": round(max(0.0, evaluation_timeout), 3),
                            "stage_elapsed_seconds": round(evaluation_elapsed, 3),
                            "remaining_wall_seconds": round(
                                max(0.0, _wall_remaining(state, clock)), 3
                            ),
                            "reserved_wall_seconds": round(
                                _finalize_reserve_seconds(state), 3
                            ),
                            "worker_may_finish_in_background": (
                                evaluation_timeout > 0.0
                            ),
                            "elapsed_seconds": round(_elapsed_seconds(state, clock), 3),
                        },
                    },
                ),
            }
        except Exception as exc:
            evaluation_elapsed = max(0.0, clock() - evaluation_started_at)
            logger.error(
                "shader.pipeline.evaluate.failed run_id=%s project_id=%s "
                "candidate_id=%s error_type=%s",
                run_id,
                project_id,
                record.candidate_id,
                type(exc).__name__,
            )
            return {
                **repair_update,
                "phase": "evaluation_failed",
                "candidate_record": rendered_record,
                "candidate_records": _replace_record(
                    tuple(state.get("candidate_records", ())), rendered_record
                ),
                "static_validation": render.compile.static_validation.to_dict(),
                "compile_result": render.compile.to_dict(),
                "render_status": "evaluation_failed",
                "rendered_image": render.image_bytes,
                "rendered_content_type": "image/png",
                "stop_reason": StopReason.COMPLETED_WITH_BEST_EFFORT.value,
                "events": (
                    *events,
                    {
                        "stage": "evaluate",
                        "event_type": "evaluation_failed",
                        "payload": {
                            "candidate_id": record.candidate_id,
                            "failure_stage": "evaluation",
                            "error_type": type(exc).__name__,
                            "stage_elapsed_seconds": round(evaluation_elapsed, 3),
                            "elapsed_seconds": round(_elapsed_seconds(state, clock), 3),
                        },
                    },
                ),
            }
        metrics_ref = store.write_json(f"{prefix}/metrics.json", score.to_dict())
        completed = replace(
            rendered_record,
            metrics_ref=metrics_ref.relative_path,
            score_summary=score,
        )
        _write_candidate_manifest(store, completed)
        stop_reason = ""
        if _wall_remaining(state, clock) <= 0.0:
            stop_reason = StopReason.WALL_TIME_EXHAUSTED.value
        logger.info(
            "shader.pipeline.evaluate.completed run_id=%s project_id=%s "
            "candidate_id=%s total_loss=%.6f wall_time_exhausted=%s",
            run_id,
            project_id,
            record.candidate_id,
            score.total_loss,
            bool(stop_reason),
        )
        return {
            **repair_update,
            "phase": "evaluated",
            "candidate_record": completed,
            "candidate_records": _replace_record(
                tuple(state.get("candidate_records", ())), completed
            ),
            "static_validation": render.compile.static_validation.to_dict(),
            "compile_result": render.compile.to_dict(),
            "render_status": "success",
            "rendered_image": render.image_bytes,
            "rendered_content_type": "image/png",
            "score_breakdown": score,
            "stop_reason": stop_reason,
            "events": (
                *events,
                {
                    "stage": "evaluate",
                    "event_type": "candidate_evaluated",
                    "payload": {
                        "candidate_id": record.candidate_id,
                        "total_loss": score.total_loss,
                        "render_sha256": render_ref.sha256,
                        "elapsed_seconds": round(_elapsed_seconds(state, clock), 3),
                    },
                },
            ),
        }

    return render_and_evaluate


def make_select_current_best_node(artifact_store: LocalArtifactStore) -> RunNode:
    """创建 current_best 单调接受节点."""

    async def select(state: Mapping[str, Any]) -> dict[str, Any]:
        candidate = _record(state["candidate_record"])
        current_raw = state.get("current_best_record")
        current = None if current_raw is None else _record(current_raw)
        decision = select_current_best(current, candidate, _acceptance(state))
        store = _run_store(artifact_store, state)
        decision_ref = store.write_json(
            f"candidates/{candidate.candidate_id}/selection.json",
            decision.to_dict(),
        )
        update: dict[str, Any] = {
            "phase": "candidate_selected",
            "selection_decision": decision.to_dict(),
            "selection_ref": decision_ref.relative_path,
            "events": (
                *state.get("events", ()),
                {
                    "stage": "selection",
                    "event_type": (
                        "current_best_updated"
                        if decision.accepted
                        else "candidate_rejected"
                    ),
                    "payload": {
                        "candidate_id": candidate.candidate_id,
                        **decision.to_dict(),
                    },
                },
            ),
        }
        if decision.accepted:
            if candidate.score_summary is None:
                raise RuntimeError("被接受候选缺少 score_summary。")
            update.update(
                {
                    "current_best_record": candidate,
                    "current_best_id": candidate.candidate_id,
                    "current_best_glsl_sha256": candidate.glsl_sha256,
                    "current_best_total_loss": candidate.score_summary.total_loss,
                    "current_best_score_summary": candidate.score_summary.to_dict(),
                    "no_improvement_count": 0,
                    "iteration": candidate.iteration,
                }
            )
        elif current is not None:
            if current.score_summary is None:
                raise RuntimeError("既有 current_best 缺少 score_summary。")
            update.update(
                {
                    "current_best_record": current,
                    "current_best_id": current.candidate_id,
                    "current_best_glsl_sha256": current.glsl_sha256,
                    "current_best_total_loss": current.score_summary.total_loss,
                    "current_best_score_summary": current.score_summary.to_dict(),
                    "iteration": current.iteration,
                }
            )
            if candidate.origin != "deterministic":
                update["no_improvement_count"] = (
                    int(state.get("no_improvement_count", 0)) + 1
                )
        return update

    return select


def make_prepare_compile_repair_node() -> RunNode:
    """创建把失败候选和剩余 compile budget 绑定给 Author 的节点."""

    async def prepare(state: Mapping[str, Any]) -> dict[str, Any]:
        budget = _budget(state)
        used = int(state.get("compile_repair_count", 0))
        return {
            "phase": "compile_repair_prepared",
            "previous_author_result": dict(state["author_result"]),
            "repair_budget": {
                "used": used,
                "remaining": max(0, budget.max_compile_repairs - used),
                "maximum": budget.max_compile_repairs,
            },
        }

    return prepare


def _residual_summary(score: ScoreBreakdownV1) -> dict[str, Any]:
    worst_rois = sorted(score.roi_losses, key=lambda item: (-item[1], item[0]))[:3]
    return {
        "total_loss": score.total_loss,
        "global_rmse": score.global_rmse,
        "edge_loss": score.edge_loss,
        "geometry_loss": score.geometry_loss,
        "worst_rois": [
            {"region_id": region_id, "loss": loss} for region_id, loss in worst_rois
        ],
        "diagnostics": list(score.diagnostics),
    }


def make_load_current_best_node(artifact_store: LocalArtifactStore) -> RunNode:
    """创建只从 current_best Artifact 恢复 Critic/Refine 输入的节点."""

    async def load(state: Mapping[str, Any]) -> dict[str, Any]:
        best = _record(state["current_best_record"])
        if (
            not best.hard_constraints_passed
            or best.score_summary is None
            or best.render_ref is None
        ):
            raise RuntimeError("current_best 不是可运行候选。")
        store = _run_store(artifact_store, state)
        glsl = store.read_bytes(best.glsl_ref).decode("utf-8")
        rendered = store.read_bytes(best.render_ref)
        if sha256(glsl.encode("utf-8")).hexdigest() != best.glsl_sha256:
            raise RuntimeError("current_best GLSL Artifact hash 不一致。")
        if sha256(rendered).hexdigest() != best.render_sha256:
            raise RuntimeError("current_best Render Artifact hash 不一致。")
        author = _read_json(store, best.author_ref)
        candidate_input = CandidateRecordInput(
            candidate_id=best.candidate_id,
            parent_candidate_id=best.parent_candidate_id,
            glsl_sha256=best.glsl_sha256,
            render_sha256=best.render_sha256,
            prompt_version=best.prompt_version,
            model_ref=best.model_ref,
            iteration=best.iteration,
            origin=best.origin,
            generator_version=best.generator_version,
        ).to_dict()
        binding = {
            "candidate_id": best.candidate_id,
            "glsl_sha256": best.glsl_sha256,
            "image_sha256": best.render_sha256,
        }
        return {
            "phase": "current_best_loaded",
            "candidate_record": best,
            "author_result": author,
            "glsl": glsl,
            "rendered_image": rendered,
            "rendered_content_type": "image/png",
            "score_breakdown": best.score_summary.to_dict(),
            "residual_summary": _residual_summary(best.score_summary),
            "current_candidate": candidate_input,
            "current_best_candidate": candidate_input,
            "render_evidence_binding": binding,
        }

    return load


def make_persist_visual_review_node(artifact_store: LocalArtifactStore) -> RunNode:
    """创建把 Critic 结果绑定并写回 current_best manifest 的节点."""

    async def persist(state: Mapping[str, Any]) -> dict[str, Any]:
        best = _record(state["current_best_record"])
        review = dict(state["visual_review"])
        if str(review["candidate_id"]) != best.candidate_id:
            raise ValueError("VisualReview 未绑定 current_best。")
        store = _run_store(artifact_store, state)
        review_sequence = int(state.get("visual_refinement_count", 0)) + 1
        review_ref = store.write_json(
            f"candidates/{best.candidate_id}/reviews/review-{review_sequence:04d}.json",
            review,
        )
        updated = replace(best, review_ref=review_ref.relative_path)
        _write_candidate_manifest(store, updated)
        return {
            "phase": "review_persisted",
            "current_best_record": updated,
            "candidate_record": updated,
            "candidate_records": _replace_record(
                tuple(state.get("candidate_records", ())), updated
            ),
            "events": (
                *state.get("events", ()),
                {
                    "stage": "visual_critic",
                    "event_type": "review_persisted",
                    "payload": {
                        "candidate_id": best.candidate_id,
                        "artifact_ref": review_ref.relative_path,
                        "review_sequence": review_sequence,
                    },
                },
            ),
        }

    return persist


def _latest_validated_fallback(state: Mapping[str, Any]) -> CandidateRecord | None:
    """返回最近通过静态检查与真实 WebGL 渲染、但可能未评分的候选."""
    for value in reversed(tuple(state.get("candidate_records", ()))):
        candidate = _record(value)
        if (
            candidate.hard_constraints_passed
            and candidate.render_ref is not None
            and candidate.render_sha256 is not None
        ):
            return candidate
    return None


def make_finalize_png_to_shader_v1_node(
    artifact_store: LocalArtifactStore,
    renderer_registry: RunRendererRegistry,
    *,
    clock: Clock,
) -> RunNode:
    """创建永远从 current_best Artifact 组装最终结果的节点."""

    async def finalize(state: Mapping[str, Any]) -> dict[str, Any]:
        store = _run_store(artifact_store, state)
        best_raw = state.get("current_best_record")
        best = None if best_raw is None else _record(best_raw)
        unscored_fallback = False
        if best is None:
            best = _latest_validated_fallback(state)
            unscored_fallback = best is not None and best.score_summary is None
        reason = str(
            state.get("stop_reason") or StopReason.COMPLETED_WITH_BEST_EFFORT.value
        )
        if unscored_fallback:
            reason = StopReason.COMPLETED_WITH_BEST_EFFORT.value
        result: dict[str, Any]
        final_render: bytes | None = None
        if best is None:
            result = {
                "success": False,
                "candidate_id": None,
                "glsl": None,
                "glsl_sha256": None,
                "render_ref": None,
                "render_sha256": None,
                "score_breakdown": None,
            }
        else:
            if (
                not best.hard_constraints_passed
                or best.render_ref is None
                or best.render_sha256 is None
            ):
                raise RuntimeError("finalize 拒绝不完整的 current_best。")
            glsl_bytes = store.read_bytes(best.glsl_ref)
            final_render = store.read_bytes(best.render_ref)
            if sha256(glsl_bytes).hexdigest() != best.glsl_sha256:
                raise RuntimeError("finalize 读取的 GLSL hash 不一致。")
            if sha256(final_render).hexdigest() != best.render_sha256:
                raise RuntimeError("finalize 读取的 Render hash 不一致。")
            metrics: dict[str, Any] | None = None
            if best.score_summary is not None:
                if best.metrics_ref is None:
                    raise RuntimeError("已评分 current_best 缺少 metrics Artifact。")
                metrics = _read_json(store, best.metrics_ref)
                if float(metrics["total_loss"]) != best.score_summary.total_loss:
                    raise RuntimeError(
                        "finalize 读取的 metrics 与 current_best 不一致。"
                    )
            final_glsl_ref = store.write_bytes(
                "final/shader.frag",
                glsl_bytes,
                content_type="text/x-glsl; charset=utf-8",
            )
            final_render_ref = store.write_bytes(
                "final/render.png",
                final_render,
                content_type="image/png",
            )
            final_metrics_ref = (
                store.write_json("final/metrics.json", metrics)
                if metrics is not None
                else None
            )
            result = {
                "success": True,
                "candidate_id": best.candidate_id,
                "glsl": glsl_bytes.decode("utf-8"),
                "glsl_sha256": best.glsl_sha256,
                "glsl_ref": final_glsl_ref.relative_path,
                "render_ref": final_render_ref.relative_path,
                "render_sha256": best.render_sha256,
                "metrics_ref": (
                    final_metrics_ref.relative_path
                    if final_metrics_ref is not None
                    else None
                ),
                "score_breakdown": metrics,
                "unscored_fallback": unscored_fallback,
            }

        measurements = state["target_measurements"]
        if isinstance(measurements, Mapping):
            render_width = int(measurements["analysis_width"])
            render_height = int(measurements["analysis_height"])
        else:
            render_width = int(measurements.analysis_width)
            render_height = int(measurements.analysis_height)
        result.update(
            {
                "schema_version": 1,
                "project_id": str(state["project_id"]),
                "run_id": str(state["run_id"]),
                "stop_reason": reason,
                "candidate_count": len(tuple(state.get("candidate_records", ()))),
                "model_call_count": int(state.get("model_call_count", 0)),
                "compile_repair_count": int(state.get("compile_repair_count", 0)),
                "visual_refinement_count": int(state.get("visual_refinement_count", 0)),
                "no_improvement_count": int(state.get("no_improvement_count", 0)),
                "render_width": render_width,
                "render_height": render_height,
                "elapsed_seconds": max(
                    0.0,
                    clock() - float(state["started_at"]),
                ),
            }
        )
        manifest_value = {key: value for key, value in result.items() if key != "glsl"}
        manifest = store.write_json("final/manifest.json", manifest_value)
        result["manifest_ref"] = manifest.relative_path
        events = tuple(state.get("events", ()))
        if unscored_fallback and best is not None:
            events = (
                *events,
                {
                    "stage": "finalize",
                    "event_type": "validated_candidate_fallback_selected",
                    "payload": {
                        "candidate_id": best.candidate_id,
                        "reason": "evaluation_unavailable",
                        "elapsed_seconds": round(_elapsed_seconds(state, clock), 3),
                    },
                },
            )
        try:
            await asyncio.wait_for(
                renderer_registry.close(_run_key(state)),
                timeout=RENDERER_CLOSE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            events = (
                *events,
                {
                    "stage": "finalize",
                    "event_type": "renderer_close_failed",
                    "payload": {
                        "error_type": "TimeoutError",
                        "timeout_seconds": RENDERER_CLOSE_TIMEOUT_SECONDS,
                        "elapsed_seconds": round(_elapsed_seconds(state, clock), 3),
                    },
                },
            )
        except Exception as exc:
            events = (
                *events,
                {
                    "stage": "finalize",
                    "event_type": "renderer_close_failed",
                    "payload": {"error_type": type(exc).__name__},
                },
            )
        logger.info(
            "shader.pipeline.finalized run_id=%s project_id=%s success=%s "
            "stop_reason=%s candidate_id=%s candidate_count=%s model_call_count=%s "
            "elapsed_seconds=%.3f",
            state["run_id"],
            state["project_id"],
            result["success"],
            reason,
            result["candidate_id"],
            result["candidate_count"],
            result["model_call_count"],
            result["elapsed_seconds"],
        )
        return {
            "phase": "finalized",
            "stop_reason": reason,
            "final_result": result,
            "final_manifest_ref": manifest.relative_path,
            "rendered_image": final_render or b"",
            "events": (
                *events,
                {
                    "stage": "finalize",
                    "event_type": "run_finalized",
                    "payload": {
                        "success": result["success"],
                        "candidate_id": result["candidate_id"],
                        "stop_reason": reason,
                        "manifest_ref": manifest.relative_path,
                    },
                },
            ),
        }

    return finalize
