"""运行 fixture/no-model V2.3 production Graph development/validation conformance。."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from importlib import import_module
from io import BytesIO
from pathlib import Path
from typing import Any, Literal, Sequence, cast

from langsmith import tracing_context
from PIL import Image
from pydantic import BaseModel

from agent.app.graphs.png_to_shader_v2_builder import build_png_to_shader_v2_graph
from agent.app.nodes.png_to_shader_v2 import build_png_to_shader_v2_fixture_runtime
from agent.app.states.png_to_shader_v2_state import (
    BudgetStateV2,
    BudgetVectorV2,
    PngToShaderV2State,
    build_checkpoint_namespace_v2,
)
from agent.app.states.png_to_shader_v2_state_store import (
    LocalPngToShaderV2StateStore,
    V2StateRevisionConflictError,
)
from shaderforge.analysis import TargetMeasurementsV2, measure_target_v2
from shaderforge.benchmark import (
    LoadedV2Dataset,
    V2DatasetSample,
    V2DatasetStageGate,
    evaluate_v2_dataset_stage_gate,
    load_v2_dataset_manifest,
)
from shaderforge.benchmark.v2_3_graph_gate import (
    V2_3_RESTART_PHASES,
    V2_3GraphCaseOutcome,
    V2_3GraphFailureCode,
    V2_3GraphGateReport,
    V2_3RestartPhase,
    V2_3RestartPhaseOutcome,
    V2_3TerminalClass,
    evaluate_v2_3_graph_gate,
)
from shaderforge.contracts import REQUIRED_LAYER_ORDER, canonical_sha256
from shaderforge.evaluation import (
    BEAUTY_CAPTURE_COUNT,
    load_candidate_attempt,
    load_typed_candidate_artifacts,
)
from shaderforge.genome import TypedEffectGenome, compute_genome_hashes
from shaderforge.intent import IntentBuildContext, IntentIR
from shaderforge.rendering import CompileResult, RendererMetadata, RenderResult
from shaderforge.store import ArtifactRefV2, LocalArtifactCatalog, LocalArtifactStore
from shaderforge.validation import validate_shader

try:
    intent_runner = import_module("scripts.run_v2_1_intent_benchmark")
    compiler_runner = import_module("scripts.run_v2_2_compiler_benchmark")
except ModuleNotFoundError:
    intent_runner = import_module("run_v2_1_intent_benchmark")
    compiler_runner = import_module("run_v2_2_compiler_benchmark")

RUNNER_VERSION: Literal["v2_3_graph_fixture_benchmark_v2"] = (
    "v2_3_graph_fixture_benchmark_v2"
)
EXECUTION_MODE: Literal["fixture/no-model"] = "fixture/no-model"
PROJECT_ID = "v2-3-graph-benchmark"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "benchmarks/png_to_shader_v2/dataset_manifest.v1.json"
DEFAULT_BENCHMARK_ROOT = ROOT / "benchmarks"


@dataclass(frozen=True)
class V2_3GraphBenchmarkRun:
    """一次 V2.3 Graph conformance 的本地结果。."""

    output_dir: Path
    config: dict[str, object]
    outcomes: tuple[V2_3GraphCaseOutcome, ...]
    report: V2_3GraphGateReport
    summary: dict[str, object]


class _FixtureRenderer:
    """Graph 控制流 fixture：验证 GLSL 后返回冻结 reference PNG。."""

    def __init__(self, reference_png: bytes) -> None:
        self._reference_png = reference_png

    async def render(
        self, fragment_source: str, width: int, height: int
    ) -> RenderResult:
        validation = validate_shader(fragment_source)
        image_bytes: bytes | None = None
        if validation.valid:
            with Image.open(BytesIO(self._reference_png)) as source:
                if source.size == (width, height):
                    image_bytes = self._reference_png
                else:
                    resized = source.resize((width, height), Image.Resampling.NEAREST)
                    output = BytesIO()
                    resized.save(output, format="PNG")
                    image_bytes = output.getvalue()
        compile_result = CompileResult(
            success=validation.valid,
            vertex_log="",
            fragment_log="",
            link_log="",
            draw_error=None if validation.valid else "static_validation_failed",
            static_validation=validation,
        )
        return RenderResult(
            success=validation.valid,
            image_bytes=image_bytes,
            width=width,
            height=height,
            compile=compile_result,
            console_errors=(),
            metadata=(
                RendererMetadata(
                    renderer_version="v2-3-graph-fixture",
                    browser_version="fixture",
                    gl_version="WebGL 1.0",
                    glsl_version="WebGL GLSL ES 1.00",
                    gl_vendor="fixture",
                    gl_renderer="fixture",
                    webgl_context_kind="webgl1",
                    canvas_alpha=False,
                    canvas_antialias=False,
                    canvas_depth=False,
                    canvas_stencil=False,
                    premultiplied_alpha=False,
                    preserve_drawing_buffer=True,
                    canvas_clear_color_rgba=(0.0, 0.0, 0.0, 1.0),
                )
                if validation.valid
                else None
            ),
            duration_ms=0.0,
        )

    async def close(self) -> None:
        return None


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, indent=2, default=_json_default
        )
        + "\n",
        encoding="utf-8",
    )


def _json_default(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"{type(value).__name__} 不能编码为 benchmark JSON。")


def _put_json(
    catalog: LocalArtifactCatalog,
    *,
    run_id: str,
    kind: str,
    schema_version: str,
    value: object,
) -> ArtifactRefV2:
    return catalog.put(
        run_id=run_id,
        kind=kind,
        schema_version=schema_version,
        content_type="application/json",
        data=json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=_json_default,
        ).encode("utf-8"),
    )


def _zero_budget() -> BudgetVectorV2:
    return BudgetVectorV2(
        wall_time_ms=0,
        model_calls=0,
        model_tokens=0,
        render_calls=0,
        candidate_attempts=0,
        artifact_bytes=0,
        cost_usd_micros=0,
    )


def _fixture_render_call_budget(
    measurements: TargetMeasurementsV2, *, expected_attempts: int
) -> int:
    """为冻结 seed 的完整 multi-capture plan 预留确定性上界。."""
    max_instance_count = max(
        item.instance_count for item in measurements.target_hypotheses
    )
    calls_per_attempt = (
        BEAUTY_CAPTURE_COUNT
        + 1  # subject visible-delta
        + max_instance_count
        + len(REQUIRED_LAYER_ORDER)
    )
    return expected_attempts * calls_per_attempt


def _semantic_state_projection(
    state: PngToShaderV2State,
    catalog: LocalArtifactCatalog,
) -> dict[str, object]:
    summaries: list[dict[str, object]] = []
    for ref in state.candidate_summary_refs:
        summary: dict[str, object] = {"kind": ref.kind}
        if ref.kind == "candidate_record":
            loaded = load_typed_candidate_artifacts(
                ref, resolver=catalog, run_id=state.run_id
            )
            summary.update(
                {
                    "target_hypothesis_hash": loaded.candidate.target_hypothesis_hash,
                    "semantic_genome_hash": loaded.candidate.semantic_genome_hash,
                    "total_loss": loaded.basic_evaluations[-1].total_loss,
                }
            )
        else:
            attempt = load_candidate_attempt(
                ref, resolver=catalog, run_id=state.run_id
            ).attempt
            summary.update(
                {
                    "target_hypothesis_hash": attempt.target_hypothesis_hash,
                    "semantic_genome_hash": attempt.semantic_genome_hash,
                    "status": attempt.status,
                    "error_code": attempt.error_code,
                }
            )
        summaries.append(summary)
    return {
        "phase": state.phase,
        "evaluation_revision": state.evaluation_revision,
        "hypothesis_cursor": state.hypothesis_cursor,
        "hypothesis_branches": tuple(
            {
                "target_hypothesis_id": branch.target_hypothesis_id,
                "target_hypothesis_hash": branch.target_hypothesis_hash,
                "seed_semantic_hashes": tuple(
                    compute_genome_hashes(
                        TypedEffectGenome.model_validate_json(
                            catalog.read_bytes(ref.artifact_id), strict=True
                        )
                    ).semantic_genome_hash
                    for ref in branch.seed_refs
                ),
                "seed_cursor": branch.seed_cursor,
                "has_branch_best": branch.hypothesis_best_id is not None,
                "status": branch.status,
            }
            for branch in state.hypothesis_branches
        ),
        "has_objective_best": state.objective_best_ref is not None,
        "candidate_summaries": tuple(summaries),
        # Artifact JSON 内含 run-local id/ref，跨新 run 的字节数可不同；语义 replay
        # 只比较副作用次数/模型与成本账本，逐 run Artifact bytes 由各自 Manifest 锚定。
        "budget_used": {
            name: getattr(state.budget_state.used, name)
            for name in (
                "model_calls",
                "model_tokens",
                "render_calls",
                "candidate_attempts",
                "cost_usd_micros",
            )
        },
        "budget_reserved": {
            name: getattr(state.budget_state.reserved, name)
            for name in (
                "model_calls",
                "model_tokens",
                "render_calls",
                "candidate_attempts",
                "cost_usd_micros",
            )
        },
        "stop_reason": state.stop_reason,
    }


def _inspect_attempt_closure(
    state: PngToShaderV2State,
    catalog: LocalArtifactCatalog,
) -> tuple[int, int, tuple[str, ...], bool, int]:
    expected: dict[tuple[str, str, str], ArtifactRefV2] = {}
    for branch in state.hypothesis_branches:
        for seed_ref in branch.seed_refs:
            if catalog.resolve(seed_ref.artifact_id) != seed_ref:
                raise ValueError("Seed Genome ref 与 Catalog 不一致。")
            genome = TypedEffectGenome.model_validate_json(
                catalog.read_bytes(seed_ref.artifact_id), strict=True
            )
            semantic_hash = compute_genome_hashes(genome).semantic_genome_hash
            key = (
                branch.target_hypothesis_id,
                branch.target_hypothesis_hash,
                semantic_hash,
            )
            if (
                genome.provenance.target_hypothesis_id != key[0]
                or genome.provenance.target_hypothesis_hash != key[1]
            ):
                raise ValueError("Seed Genome 与 branch hypothesis identity 错绑。")
            if key in expected:
                raise ValueError("期望 Candidate attempt identity 重复。")
            expected[key] = seed_ref

    summary_ids = [ref.artifact_id for ref in state.candidate_summary_refs]
    if len(summary_ids) != len(set(summary_ids)):
        raise ValueError("Candidate/Attempt summary ref 不得重复。")
    seen: dict[tuple[str, str, str], Literal["candidate", "attempt"]] = {}
    attempt_ids: set[str] = set()
    successful = 0
    unsupported = 0
    reason_codes: list[str] = []
    closed = 0
    for ref in state.candidate_summary_refs:
        if ref.kind == "candidate_record":
            loaded = load_typed_candidate_artifacts(
                ref, resolver=catalog, run_id=state.run_id
            )
            key = (
                loaded.candidate.target_hypothesis_id,
                loaded.candidate.target_hypothesis_hash,
                loaded.candidate.semantic_genome_hash,
            )
            expected_genome_ref = expected.get(key)
            if expected_genome_ref is None or loaded.candidate.genome_ref != (
                expected_genome_ref
            ):
                raise ValueError("Candidate 与期望 branch/semantic Genome 错绑。")
            provenance = loaded.provenance
            if provenance.attempt_id is None or not provenance.attempt_evidence_refs:
                raise ValueError("Candidate 缺少正式 attempt/evidence 闭包。")
            attempt_id = provenance.attempt_id
            if attempt_id in attempt_ids or key in seen:
                raise ValueError("每个期望 attempt 必须恰好闭合一次。")
            attempt_ids.add(attempt_id)
            seen[key] = "candidate"
            successful += 1
            closed += 1
            continue
        if ref.kind != "candidate_attempt_record":
            raise ValueError("summary 只能是正式 Candidate 或 CandidateAttemptRecord。")
        loaded_attempt = load_candidate_attempt(
            ref,
            resolver=catalog,
            run_id=state.run_id,
        )
        matches = tuple(
            key
            for key in expected
            if key[1] == loaded_attempt.attempt.target_hypothesis_hash
            and key[2] == loaded_attempt.attempt.semantic_genome_hash
        )
        if len(matches) != 1:
            raise ValueError("CandidateAttemptRecord 无法唯一绑定期望 attempt。")
        key = matches[0]
        loaded_attempt = load_candidate_attempt(
            ref,
            resolver=catalog,
            run_id=state.run_id,
            expected_target_hypothesis_hash=key[1],
            expected_semantic_genome_hash=key[2],
        )
        if loaded_attempt.attempt.attempt_id in attempt_ids or key in seen:
            raise ValueError("每个期望 attempt 必须恰好闭合一次。")
        attempt_ids.add(loaded_attempt.attempt.attempt_id)
        seen[key] = "attempt"
        reason_codes.append(loaded_attempt.attempt.error_code)
        if loaded_attempt.attempt.error_code == (
            "target_structure_requires_typed_topology_receipt"
        ):
            unsupported += 1
        closed += 1
    identity_ok = set(seen) == set(expected) and len(seen) == len(expected)
    return (
        closed,
        successful,
        tuple(sorted(set(reason_codes))),
        identity_ok,
        unsupported,
    )


def _classify_hypothesis_capabilities(
    state: PngToShaderV2State,
    catalog: LocalArtifactCatalog,
) -> tuple[int, int, str]:
    """按实际可行 Intent branch 冻结当前 typed topology capability。."""
    evidence: list[dict[str, object]] = []
    supported = 0
    for branch in state.hypothesis_branches:
        intent = IntentIR.model_validate_json(
            catalog.read_bytes(branch.intent_ref.artifact_id), strict=True
        )
        branch_supported = (
            len(intent.objects) == 1
            and intent.objects[0].topology == "solid"
            and intent.objects[0].component_count == 1
            and intent.objects[0].instance_count == 1
            and intent.objects[0].hole_count == 0
        )
        supported += branch_supported
        evidence.append(
            {
                "target_hypothesis_id": branch.target_hypothesis_id,
                "target_hypothesis_hash": branch.target_hypothesis_hash,
                "capability": (
                    "solid_single_instance_proven"
                    if branch_supported
                    else "requires_typed_topology_receipt"
                ),
            }
        )
    return (
        supported,
        len(state.hypothesis_branches) - supported,
        canonical_sha256(evidence),
    )


class _InjectedPhaseCrash(RuntimeError):
    """测试进程在 checkpoint 已确认后模拟退出。."""


def _matches_restart_phase(
    state: PngToShaderV2State, phase: V2_3RestartPhase
) -> bool:
    if phase == "measured":
        return state.phase == "measured"
    if phase == "interpreted":
        return state.phase == "interpreted"
    if phase == "seeding":
        return state.phase == "seeding" and state.active_genome_ref is not None
    if phase == "compiled":
        return state.phase == "compiling" and state.active_compilation_ref is not None
    if phase == "rendered":
        return (
            state.phase == "rendering"
            and state.active_attempt_id is not None
            and state.active_compilation_ref is not None
            and state.active_diagnostic_compilation_ref is not None
            and state.active_render_plan_ref is not None
            and state.active_render_progress_ref is not None
            and state.active_render_repeatability_ref is not None
            and state.active_render_call_ordinal is None
            and state.budget_state.reserved.render_calls == 0
        )
    if phase == "evaluated":
        return (
            state.phase == "evaluating"
            and len(state.active_evaluation_refs) == BEAUTY_CAPTURE_COUNT
            and state.active_rendered_structure_verification_ref is not None
        )
    if phase == "materialized":
        return (
            state.phase == "selecting"
            and bool(state.candidate_summary_refs)
            and state.candidate_summary_refs[-1].kind == "candidate_record"
            and state.hypothesis_cursor < len(state.hypothesis_branches)
            and state.hypothesis_branches[
                state.hypothesis_cursor
            ].hypothesis_best_id
            is None
        )
    return (
        state.phase == "selecting"
        and state.hypothesis_cursor < len(state.hypothesis_branches)
        and state.hypothesis_branches[state.hypothesis_cursor].hypothesis_best_id
        is not None
    )


class _PhaseFailpointStateStore:
    """只在真实 CAS 已落盘后抛错；预算 reserve/commit 完全委托 production store。."""

    def __init__(
        self,
        delegate: LocalPngToShaderV2StateStore,
        phase: V2_3RestartPhase,
    ) -> None:
        self._delegate = delegate
        self._phase = phase
        self.triggered = False

    def initialize(self, state: PngToShaderV2State) -> PngToShaderV2State:
        return self._delegate.initialize(state)

    def load_last_confirmed(self, run_id: str) -> PngToShaderV2State:
        return self._delegate.load_last_confirmed(run_id)

    def compare_and_swap_run(
        self,
        run_id: str,
        *,
        expected_run_revision: int,
        changes: Mapping[str, Any],
    ) -> PngToShaderV2State:
        confirmed = self._delegate.compare_and_swap_run(
            run_id,
            expected_run_revision=expected_run_revision,
            changes=changes,
        )
        if not self.triggered and _matches_restart_phase(confirmed, self._phase):
            self.triggered = True
            raise _InjectedPhaseCrash(f"injected checkpoint crash: {self._phase}")
        return confirmed

    def reserve_budget(
        self,
        run_id: str,
        delta: BudgetVectorV2,
        *,
        expected_budget_revision: int,
    ) -> PngToShaderV2State:
        return self._delegate.reserve_budget(
            run_id, delta, expected_budget_revision=expected_budget_revision
        )

    def commit_budget(
        self,
        run_id: str,
        *,
        reservation: BudgetVectorV2,
        used: BudgetVectorV2,
        expected_budget_revision: int,
    ) -> PngToShaderV2State:
        return self._delegate.commit_budget(
            run_id,
            reservation=reservation,
            used=used,
            expected_budget_revision=expected_budget_revision,
        )


async def _invoke_graph(graph: object, state: PngToShaderV2State) -> PngToShaderV2State:
    # Benchmark 必须可离线复验；即使调用方 shell 打开了 LangSmith，也不得上传。
    with tracing_context(enabled=False):
        raw = await cast(Any, graph).ainvoke(state, config={"callbacks": []})
    return PngToShaderV2State.model_validate(raw, strict=True)


@dataclass(frozen=True)
class _PreparedFixtureRun:
    root: Path
    run_id: str
    artifact_root: Path
    state_store_root: Path
    initial: PngToShaderV2State
    intent_context: IntentBuildContext
    normalized_reference_artifact_id: str


def _prepare_fixture_run(
    *,
    root: Path,
    run_id: str,
    dataset: LoadedV2Dataset,
    sample: V2DatasetSample,
    split: Literal["development", "validation"],
    config: dict[str, object],
    config_sha256: str,
    source_bytes: bytes,
) -> _PreparedFixtureRun:
    artifact_root = root / "artifact-store"
    state_store_root = root / "state-store"
    store = LocalArtifactStore(artifact_root)
    catalog = LocalArtifactCatalog(
        store.register_run(PROJECT_ID, run_id), run_id=run_id
    )
    config_ref = _put_json(
        catalog,
        run_id=run_id,
        kind="v2_3_graph_benchmark_config",
        schema_version="v2_3_graph_benchmark_config_v2",
        value=config,
    )
    bundle = measure_target_v2(source_bytes, catalog=catalog, run_id=run_id)
    request_ref = _put_json(
        catalog,
        run_id=run_id,
        kind="v2_3_graph_case_request",
        schema_version="v2_3_graph_case_request_v2",
        value={
            "schema_version": "v2_3_graph_case_request_v2",
            "case_id": sample.case_id,
            "split": split,
            "source_sha256": sample.sha256,
        },
    )
    constraints = intent_runner._build_constraints(  # noqa: SLF001
        sample, bundle, request_ref, config_ref
    )
    constraint_ref = _put_json(
        catalog,
        run_id=run_id,
        kind="request_constraint_set",
        schema_version="request_constraint_set_v1",
        value=constraints,
    )
    interpretation, intent_context = intent_runner._fixture_interpretation(  # noqa: SLF001
        dataset, sample, bundle.measurements_ref
    )
    interpretation_ref = _put_json(
        catalog,
        run_id=run_id,
        kind="visual_interpretation",
        schema_version="visual_interpretation_v2_1",
        value=interpretation,
    )
    expected_attempts = len(bundle.measurements.target_hypotheses) * 3
    render_call_budget = _fixture_render_call_budget(
        bundle.measurements, expected_attempts=expected_attempts
    )
    zero = _zero_budget()
    initial = PngToShaderV2State(
        checkpoint_namespace=build_checkpoint_namespace_v2(run_id),
        project_id=PROJECT_ID,
        run_id=run_id,
        run_revision=0,
        phase="initialized",
        evaluation_revision=0,
        measurements_ref=bundle.measurements_ref,
        visual_interpretation_ref=interpretation_ref,
        request_constraint_set_ref=constraint_ref,
        hypothesis_branches=(),
        hypothesis_cursor=0,
        objective_best_id=None,
        candidate_summary_refs=(),
        budget_state=BudgetStateV2(
            policy_hash=config_sha256,
            revision=0,
            limits=BudgetVectorV2(
                wall_time_ms=0,
                model_calls=0,
                model_tokens=0,
                render_calls=render_call_budget,
                candidate_attempts=expected_attempts,
                artifact_bytes=max(
                    render_call_budget
                    * (
                        bundle.measurements.image_size[0]
                        * bundle.measurements.image_size[1]
                        * 4
                        + 65_536
                    ),
                    1,
                ),
                cost_usd_micros=0,
            ),
            used=zero,
            reserved=zero,
            exhausted_dimensions=(
                "wall_time_ms",
                "model_calls",
                "model_tokens",
                "cost_usd_micros",
            ),
        ),
        stop_reason=None,
    )
    return _PreparedFixtureRun(
        root=root,
        run_id=run_id,
        artifact_root=artifact_root,
        state_store_root=state_store_root,
        initial=initial,
        intent_context=intent_context,
        normalized_reference_artifact_id=(
            bundle.normalized_reference_ref.artifact_id
        ),
    )


def _open_fixture_dependencies(
    prepared: _PreparedFixtureRun,
    *,
    state_store: object | None = None,
) -> tuple[LocalArtifactCatalog, LocalPngToShaderV2StateStore, object]:
    store = LocalArtifactStore(prepared.artifact_root)
    catalog = LocalArtifactCatalog(
        store.resolve_run(prepared.run_id), run_id=prepared.run_id
    )
    durable_store = LocalPngToShaderV2StateStore(prepared.state_store_root)
    runtime_store = durable_store if state_store is None else state_store
    normalized_ref = catalog.resolve(prepared.normalized_reference_artifact_id)
    runtime = build_png_to_shader_v2_fixture_runtime(
        catalog_factory=lambda _state: catalog,
        intent_context_provider=lambda _state, _measurements, _interpretation, _constraints: (
            prepared.intent_context
        ),
        renderer_factory=lambda _state: _FixtureRenderer(
            catalog.read_bytes(normalized_ref.artifact_id)
        ),
        reference_artifact_provider=lambda _state, _resolver: normalized_ref,
        state_store=cast(Any, runtime_store),
    )
    return catalog, durable_store, build_png_to_shader_v2_graph(runtime)


def _budget_projection(state: PngToShaderV2State) -> dict[str, object]:
    return {
        "limits": state.budget_state.limits.model_dump(mode="python"),
        "used": state.budget_state.used.model_dump(mode="python"),
        "reserved": state.budget_state.reserved.model_dump(mode="python"),
        "exhausted_dimensions": state.budget_state.exhausted_dimensions,
    }


def _artifact_closure_projection(
    state: PngToShaderV2State, catalog: LocalArtifactCatalog
) -> dict[str, object]:
    closed, successful, reasons, identity_ok, unsupported = (
        _inspect_attempt_closure(state, catalog)
    )
    return {
        "closed": closed,
        "successful": successful,
        "reasons": reasons,
        "identity_ok": identity_ok,
        "unsupported": unsupported,
        "summaries": _semantic_state_projection(state, catalog)[
            "candidate_summaries"
        ],
    }


def _run_restart_phase_probe(
    *,
    root: Path,
    phase: V2_3RestartPhase,
    dataset: LoadedV2Dataset,
    sample: V2DatasetSample,
    split: Literal["development", "validation"],
    config: dict[str, object],
    config_sha256: str,
    source_bytes: bytes,
) -> V2_3RestartPhaseOutcome:
    phase_index = (
        "measured interpreted seeding compiled rendered evaluated materialized selected"
    ).split().index(phase)
    prefix = f"v2-3-r{phase_index}-{split[0]}-{sample.case_id}"
    control = _prepare_fixture_run(
        root=root / "control",
        run_id=f"{prefix}-c",
        dataset=dataset,
        sample=sample,
        split=split,
        config=config,
        config_sha256=config_sha256,
        source_bytes=source_bytes,
    )
    control_catalog, _, control_graph = _open_fixture_dependencies(control)
    uninterrupted = asyncio.run(_invoke_graph(control_graph, control.initial))

    interrupted = _prepare_fixture_run(
        root=root / "interrupted",
        run_id=f"{prefix}-i",
        dataset=dataset,
        sample=sample,
        split=split,
        config=config,
        config_sha256=config_sha256,
        source_bytes=source_bytes,
    )
    initial_store = LocalPngToShaderV2StateStore(interrupted.state_store_root)
    failpoint_store = _PhaseFailpointStateStore(initial_store, phase)
    _, _, interrupted_graph = _open_fixture_dependencies(
        interrupted, state_store=failpoint_store
    )
    try:
        asyncio.run(_invoke_graph(interrupted_graph, interrupted.initial))
    except _InjectedPhaseCrash:
        pass
    else:
        return V2_3RestartPhaseOutcome(
            phase=phase,
            verified=False,
            crash_state_projection_sha256=None,
            uninterrupted_final_state_sha256=None,
            resumed_final_state_sha256=None,
            side_effect_counts_match=False,
            budget_match=False,
            artifact_closure_match=False,
            cursor_match=False,
            evaluation_revision_match=False,
        )

    # 模拟进程边界：不复用 Catalog、StateStore、runtime 或 Graph 对象。
    crash_catalog = LocalArtifactCatalog(
        LocalArtifactStore(interrupted.artifact_root).resolve_run(interrupted.run_id),
        run_id=interrupted.run_id,
    )
    crash_store = LocalPngToShaderV2StateStore(interrupted.state_store_root)
    crash_state = crash_store.load_last_confirmed(interrupted.run_id)
    crash_hash = canonical_sha256(_semantic_state_projection(crash_state, crash_catalog))
    resumed_catalog, resumed_store, resumed_graph = _open_fixture_dependencies(
        interrupted
    )
    recovered = resumed_store.load_last_confirmed(interrupted.run_id)
    resumed = asyncio.run(_invoke_graph(resumed_graph, recovered))

    uninterrupted_projection = _semantic_state_projection(
        uninterrupted, control_catalog
    )
    resumed_projection = _semantic_state_projection(resumed, resumed_catalog)
    uninterrupted_hash = canonical_sha256(uninterrupted_projection)
    resumed_hash = canonical_sha256(resumed_projection)
    side_effect_counts_match = all(
        getattr(uninterrupted.budget_state.used, name)
        == getattr(resumed.budget_state.used, name)
        for name in ("model_calls", "render_calls", "candidate_attempts")
    )
    budget_match = _budget_projection(uninterrupted) == _budget_projection(resumed)
    closure_match = _artifact_closure_projection(
        uninterrupted, control_catalog
    ) == _artifact_closure_projection(resumed, resumed_catalog)
    cursor_match = (
        uninterrupted.hypothesis_cursor == resumed.hypothesis_cursor
        and tuple(
            (item.seed_cursor, item.status, item.hypothesis_best_id is not None)
            for item in uninterrupted.hypothesis_branches
        )
        == tuple(
            (item.seed_cursor, item.status, item.hypothesis_best_id is not None)
            for item in resumed.hypothesis_branches
        )
    )
    evaluation_revision_match = (
        uninterrupted.evaluation_revision == resumed.evaluation_revision
    )
    return V2_3RestartPhaseOutcome(
        phase=phase,
        verified=(
            resumed_hash == uninterrupted_hash
            and side_effect_counts_match
            and budget_match
            and closure_match
            and cursor_match
            and evaluation_revision_match
        ),
        crash_state_projection_sha256=crash_hash,
        uninterrupted_final_state_sha256=uninterrupted_hash,
        resumed_final_state_sha256=resumed_hash,
        side_effect_counts_match=side_effect_counts_match,
        budget_match=budget_match,
        artifact_closure_match=closure_match,
        cursor_match=cursor_match,
        evaluation_revision_match=evaluation_revision_match,
    )


def _failure_code(
    *,
    state: PngToShaderV2State | None,
    expected_terminal: V2_3TerminalClass,
    expected_attempts: int,
    attempt_count: int,
    closed_count: int,
    successful_count: int,
    branch_best_count: int,
    identity_ok: bool,
    replay_ok: bool,
    cas_ok: bool,
    unsupported_count: int,
    supported_hypothesis_count: int,
    unsupported_hypothesis_count: int,
    reason_codes: tuple[str, ...],
) -> V2_3GraphFailureCode | None:
    if state is None:
        return "graph_execution_failed"
    if state.phase != "finalized":
        return "terminal_state_incomplete"
    terminal_ok = (
        state.stop_reason == "completed_with_objective_best"
        if expected_terminal == "objective_best"
        else state.stop_reason == "no_valid_candidate"
    )
    if not terminal_ok:
        return "unsupported_classification_mismatch"
    if attempt_count != expected_attempts:
        return "seed_attempt_count_mismatch"
    if closed_count != expected_attempts:
        return "artifact_closure_failed"
    if not identity_ok:
        return "hypothesis_identity_mismatch"
    if expected_terminal == "objective_best" and (
        successful_count < supported_hypothesis_count
        or branch_best_count != supported_hypothesis_count
    ):
        return "artifact_closure_failed"
    if successful_count < supported_hypothesis_count:
        return "artifact_closure_failed"
    if unsupported_count != unsupported_hypothesis_count * 3:
        return "unsupported_classification_mismatch"
    if unsupported_hypothesis_count > 0 and (
        "target_structure_requires_typed_topology_receipt" not in reason_codes
    ):
        return "unsupported_classification_mismatch"
    if not replay_ok:
        return "deterministic_replay_mismatch"
    if not cas_ok:
        return "cas_evidence_missing"
    if state.budget_state.used.model_calls != 0:
        return "model_calls_nonzero"
    return None


def _run_fresh_replay(
    *,
    replay_root: Path,
    dataset: LoadedV2Dataset,
    sample: V2DatasetSample,
    split: Literal["development", "validation"],
    config: dict[str, object],
    config_sha256: str,
    source_bytes: bytes,
) -> tuple[PngToShaderV2State, LocalArtifactCatalog]:
    """在新 run id、新 Catalog 与新 StateStore 上全量重放同一冻结输入。."""
    replay_run_id = f"v2-3-replay-{split}-{sample.case_id}"
    replay_store = LocalArtifactStore(replay_root / "artifact-store")
    replay_catalog = LocalArtifactCatalog(
        replay_store.register_run(PROJECT_ID, replay_run_id), run_id=replay_run_id
    )
    replay_config_ref = _put_json(
        replay_catalog,
        run_id=replay_run_id,
        kind="v2_3_graph_benchmark_config",
        schema_version="v2_3_graph_benchmark_config_v2",
        value=config,
    )
    replay_bundle = measure_target_v2(
        source_bytes, catalog=replay_catalog, run_id=replay_run_id
    )
    replay_request_ref = _put_json(
        replay_catalog,
        run_id=replay_run_id,
        kind="v2_3_graph_case_request",
        schema_version="v2_3_graph_case_request_v2",
        value={
            "schema_version": "v2_3_graph_case_request_v2",
            "case_id": sample.case_id,
            "split": split,
            "source_sha256": sample.sha256,
        },
    )
    replay_constraints = intent_runner._build_constraints(  # noqa: SLF001
        sample, replay_bundle, replay_request_ref, replay_config_ref
    )
    replay_constraint_ref = _put_json(
        replay_catalog,
        run_id=replay_run_id,
        kind="request_constraint_set",
        schema_version="request_constraint_set_v1",
        value=replay_constraints,
    )
    replay_interpretation, replay_context = intent_runner._fixture_interpretation(  # noqa: SLF001
        dataset, sample, replay_bundle.measurements_ref
    )
    replay_interpretation_ref = _put_json(
        replay_catalog,
        run_id=replay_run_id,
        kind="visual_interpretation",
        schema_version="visual_interpretation_v2_1",
        value=replay_interpretation,
    )
    replay_attempts = len(replay_bundle.measurements.target_hypotheses) * 3
    replay_render_call_budget = _fixture_render_call_budget(
        replay_bundle.measurements, expected_attempts=replay_attempts
    )
    zero = _zero_budget()
    replay_initial = PngToShaderV2State(
        checkpoint_namespace=build_checkpoint_namespace_v2(replay_run_id),
        project_id=PROJECT_ID,
        run_id=replay_run_id,
        run_revision=0,
        phase="initialized",
        evaluation_revision=0,
        measurements_ref=replay_bundle.measurements_ref,
        visual_interpretation_ref=replay_interpretation_ref,
        request_constraint_set_ref=replay_constraint_ref,
        hypothesis_branches=(),
        hypothesis_cursor=0,
        objective_best_id=None,
        candidate_summary_refs=(),
        budget_state=BudgetStateV2(
            policy_hash=config_sha256,
            revision=0,
            limits=BudgetVectorV2(
                wall_time_ms=0,
                model_calls=0,
                model_tokens=0,
                render_calls=replay_render_call_budget,
                candidate_attempts=replay_attempts,
                artifact_bytes=max(
                    replay_render_call_budget
                    * (
                        replay_bundle.measurements.image_size[0]
                        * replay_bundle.measurements.image_size[1]
                        * 4
                        + 65_536
                    ),
                    1,
                ),
                cost_usd_micros=0,
            ),
            used=zero,
            reserved=zero,
            exhausted_dimensions=(
                "wall_time_ms",
                "model_calls",
                "model_tokens",
                "cost_usd_micros",
            ),
        ),
        stop_reason=None,
    )
    replay_state_store = LocalPngToShaderV2StateStore(replay_root / "state-store")
    replay_runtime = build_png_to_shader_v2_fixture_runtime(
        catalog_factory=lambda _state: replay_catalog,
        intent_context_provider=lambda _state, _measurements, _interpretation, _constraints: (
            replay_context
        ),
        renderer_factory=lambda _state: _FixtureRenderer(
            replay_catalog.read_bytes(
                replay_bundle.normalized_reference_ref.artifact_id
            )
        ),
        reference_artifact_provider=lambda _state, _resolver: (
            replay_bundle.normalized_reference_ref
        ),
        state_store=replay_state_store,
    )
    replayed = asyncio.run(
        _invoke_graph(build_png_to_shader_v2_graph(replay_runtime), replay_initial)
    )
    return replayed, replay_catalog


def _run_case(
    *,
    output: Path,
    dataset: LoadedV2Dataset,
    gate: V2DatasetStageGate,
    sample: V2DatasetSample,
    split: Literal["development", "validation"],
    config: dict[str, object],
    config_sha256: str,
    input_intent_outcomes_sha256: str,
    input_compiler_outcomes_sha256: str,
    restart_phases: tuple[V2_3RestartPhase, ...] = (),
) -> V2_3GraphCaseOutcome:
    run_id = f"v2-3-{split}-{sample.case_id}"
    case_root = output / "cases" / split / sample.case_id
    case_root.mkdir(parents=True, exist_ok=False)
    store = LocalArtifactStore(case_root / "artifact-store")
    catalog = LocalArtifactCatalog(
        store.register_run(PROJECT_ID, run_id), run_id=run_id
    )
    state_store_root = case_root / "state-store"
    state_store = LocalPngToShaderV2StateStore(state_store_root)
    config_ref = _put_json(
        catalog,
        run_id=run_id,
        kind="v2_3_graph_benchmark_config",
        schema_version="v2_3_graph_benchmark_config_v2",
        value=config,
    )
    source_bytes = dataset.resolve_image(sample).read_bytes()
    bundle = measure_target_v2(source_bytes, catalog=catalog, run_id=run_id)
    request_ref = _put_json(
        catalog,
        run_id=run_id,
        kind="v2_3_graph_case_request",
        schema_version="v2_3_graph_case_request_v2",
        value={
            "schema_version": "v2_3_graph_case_request_v2",
            "case_id": sample.case_id,
            "split": split,
            "source_sha256": sample.sha256,
        },
    )
    constraints = intent_runner._build_constraints(  # noqa: SLF001
        sample, bundle, request_ref, config_ref
    )
    constraint_ref = _put_json(
        catalog,
        run_id=run_id,
        kind="request_constraint_set",
        schema_version="request_constraint_set_v1",
        value=constraints,
    )
    interpretation, intent_context = intent_runner._fixture_interpretation(  # noqa: SLF001
        dataset, sample, bundle.measurements_ref
    )
    interpretation_ref = _put_json(
        catalog,
        run_id=run_id,
        kind="visual_interpretation",
        schema_version="visual_interpretation_v2_1",
        value=interpretation,
    )
    hypothesis_count = len(bundle.measurements.target_hypotheses)
    expected_attempts = hypothesis_count * 3
    render_call_budget = _fixture_render_call_budget(
        bundle.measurements, expected_attempts=expected_attempts
    )
    zero = _zero_budget()
    initial = PngToShaderV2State(
        checkpoint_namespace=build_checkpoint_namespace_v2(run_id),
        project_id=PROJECT_ID,
        run_id=run_id,
        run_revision=0,
        phase="initialized",
        evaluation_revision=0,
        measurements_ref=bundle.measurements_ref,
        visual_interpretation_ref=interpretation_ref,
        request_constraint_set_ref=constraint_ref,
        hypothesis_branches=(),
        hypothesis_cursor=0,
        objective_best_id=None,
        candidate_summary_refs=(),
        budget_state=BudgetStateV2(
            policy_hash=config_sha256,
            revision=0,
            limits=BudgetVectorV2(
                wall_time_ms=0,
                model_calls=0,
                model_tokens=0,
                render_calls=render_call_budget,
                candidate_attempts=expected_attempts,
                artifact_bytes=max(
                    render_call_budget
                    * (
                        bundle.measurements.image_size[0]
                        * bundle.measurements.image_size[1]
                        * 4
                        + 65_536
                    ),
                    1,
                ),
                cost_usd_micros=0,
            ),
            used=zero,
            reserved=zero,
            exhausted_dimensions=(
                "wall_time_ms",
                "model_calls",
                "model_tokens",
                "cost_usd_micros",
            ),
        ),
        stop_reason=None,
    )
    runtime = build_png_to_shader_v2_fixture_runtime(
        catalog_factory=lambda _state: catalog,
        intent_context_provider=lambda _state, _measurements, _interpretation, _constraints: (
            intent_context
        ),
        renderer_factory=lambda _state: _FixtureRenderer(
            catalog.read_bytes(bundle.normalized_reference_ref.artifact_id)
        ),
        reference_artifact_provider=lambda _state, _resolver: (
            bundle.normalized_reference_ref
        ),
        state_store=state_store,
    )
    graph = build_png_to_shader_v2_graph(runtime)
    final: PngToShaderV2State | None = None
    replay_hash: str | None = None
    final_hash: str | None = None
    replay_ok = False
    cas_ok = False
    try:
        final = asyncio.run(_invoke_graph(graph, initial))
        projection = _semantic_state_projection(final, catalog)
        final_hash = canonical_sha256(projection)
        restarted_store = LocalPngToShaderV2StateStore(state_store_root)
        try:
            restarted_store.compare_and_swap_run(
                run_id,
                expected_run_revision=max(final.run_revision - 1, 0),
                changes={},
            )
        except V2StateRevisionConflictError:
            cas_ok = True
        replayed, replay_catalog = _run_fresh_replay(
            replay_root=case_root / "deterministic-replay",
            dataset=dataset,
            sample=sample,
            split=split,
            config=config,
            config_sha256=config_sha256,
            source_bytes=source_bytes,
        )
        replay_hash = canonical_sha256(
            _semantic_state_projection(replayed, replay_catalog)
        )
        replay_ok = replay_hash == final_hash
    except (OSError, RuntimeError, TypeError, ValueError):
        pass

    closed = 0
    successful = 0
    reasons: tuple[str, ...] = ()
    identity_ok = False
    unsupported = 0
    if final is not None:
        try:
            closed, successful, reasons, identity_ok, unsupported = (
                _inspect_attempt_closure(final, catalog)
            )
        except (FileNotFoundError, TypeError, ValueError, json.JSONDecodeError):
            pass
    branch_best_count = (
        0
        if final is None
        else sum(
            branch.hypothesis_best_id is not None
            for branch in final.hypothesis_branches
        )
    )
    attempt_count = 0 if final is None else final.budget_state.used.candidate_attempts
    if final is not None:
        hypothesis_count = len(final.hypothesis_branches)
        expected_attempts = hypothesis_count * 3
        supported_hypotheses, unsupported_hypotheses, capability_sha256 = (
            _classify_hypothesis_capabilities(final, catalog)
        )
        state_ids = tuple(
            item.target_hypothesis_id for item in final.hypothesis_branches
        )
        state_hashes = tuple(
            item.target_hypothesis_hash for item in final.hypothesis_branches
        )
    else:
        supported_hypotheses = sum(
            item.fill_topology == "solid"
            and item.component_count == 1
            and item.instance_count == 1
            and item.hole_count == 0
            for item in bundle.measurements.target_hypotheses
        )
        unsupported_hypotheses = hypothesis_count - supported_hypotheses
        state_ids = tuple(
            item.hypothesis_id for item in bundle.measurements.target_hypotheses
        )
        state_hashes = tuple(
            item.hypothesis_hash for item in bundle.measurements.target_hypotheses
        )
        capability_sha256 = canonical_sha256(
            tuple(zip(state_ids, state_hashes, strict=True))
        )
    expected_terminal: V2_3TerminalClass = (
        "objective_best"
        if supported_hypotheses > 0
        else "unsupported_no_valid_candidate"
    )
    failure_code = _failure_code(
        state=final,
        expected_terminal=expected_terminal,
        expected_attempts=expected_attempts,
        attempt_count=attempt_count,
        closed_count=closed,
        successful_count=successful,
        branch_best_count=branch_best_count,
        identity_ok=identity_ok,
        replay_ok=replay_ok,
        cas_ok=cas_ok,
        unsupported_count=unsupported,
        supported_hypothesis_count=supported_hypotheses,
        unsupported_hypothesis_count=unsupported_hypotheses,
        reason_codes=reasons,
    )
    manifest_path = (
        store.resolve_run(run_id).root / ".artifact-catalog-v2/manifest.json"
    )
    manifest_sha = (
        sha256(manifest_path.read_bytes()).hexdigest()
        if manifest_path.is_file()
        else None
    )
    unsupported_verified = (
        unsupported_hypotheses > 0
        and unsupported == unsupported_hypotheses * 3
        and "target_structure_requires_typed_topology_receipt" in reasons
    )
    restart_results = tuple(
        _run_restart_phase_probe(
            root=case_root / "restart-matrix" / phase,
            phase=phase,
            dataset=dataset,
            sample=sample,
            split=split,
            config=config,
            config_sha256=config_sha256,
            source_bytes=source_bytes,
        )
        for phase in restart_phases
    )
    outcome = V2_3GraphCaseOutcome(
        manifest_id=gate.manifest_id,
        dataset_version=gate.dataset_version,
        manifest_sha256=gate.manifest_sha256,
        taxonomy_sha256=gate.taxonomy_sha256,
        config_sha256=config_sha256,
        input_intent_outcomes_sha256=input_intent_outcomes_sha256,
        input_compiler_outcomes_sha256=input_compiler_outcomes_sha256,
        split=split,
        case_id=sample.case_id,
        success=failure_code is None,
        expected_terminal_class=expected_terminal,
        supported_hypothesis_count=supported_hypotheses,
        unsupported_hypothesis_count=unsupported_hypotheses,
        hypothesis_capability_evidence_sha256=capability_sha256,
        terminal_phase=None if final is None else final.phase,
        stop_reason=None if final is None else final.stop_reason,
        final_state_sha256=final_hash,
        replay_final_state_sha256=replay_hash,
        expected_seed_attempt_count=expected_attempts,
        seed_attempt_count=attempt_count,
        attempt_artifact_closure_count=closed,
        successful_candidate_count=successful,
        branch_best_count=branch_best_count,
        unsupported_attempt_count=unsupported,
        unsupported_reason_codes=reasons if unsupported_verified else (),
        unsupported_classification_verified=unsupported_verified,
        artifact_manifest_sha256=manifest_sha,
        hypothesis_count=hypothesis_count,
        hypothesis_ids=state_ids,
        hypothesis_hashes=state_hashes,
        hypothesis_identity_propagated=identity_ok,
        restart_phase_results=restart_results,
        deterministic_replay_verified=replay_ok,
        cas_stale_write_rejected=cas_ok,
        production_admission_enabled=False,
        model_calls=0 if final is None else final.budget_state.used.model_calls,
        failure_code=failure_code,
    )
    _write_json(case_root / "outcome.json", outcome.model_dump(mode="json"))
    return outcome


def run_v2_3_graph_benchmark(
    output_dir: str | Path,
    *,
    manifest_path: str | Path = DEFAULT_MANIFEST,
    benchmark_root: str | Path = DEFAULT_BENCHMARK_ROOT,
    execution_mode: str = EXECUTION_MODE,
    allow_model_calls: bool = False,
    enable_real_model: bool = False,
    model_call_budget: int = 0,
) -> V2_3GraphBenchmarkRun:
    """运行 production Graph；当前仅实现 fixture/no-model 明确边界。."""
    if execution_mode != EXECUTION_MODE:
        if not allow_model_calls or not enable_real_model or model_call_budget <= 0:
            raise ValueError("真实模型必须同时启用双显式开关并提供正的完整预算。")
        raise ValueError(
            "strict conformance runner 固定零模型；real 必须使用独立的 "
            "v2_3_real_model_validation runner 与 durable provider factory。"
        )
    if allow_model_calls or enable_real_model or model_call_budget != 0:
        raise ValueError("fixture/no-model 禁止模型开关或非零模型预算。")

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    dataset = load_v2_dataset_manifest(
        manifest_path,
        benchmark_root=benchmark_root,
        gate_stage="v2_3_graph_conformance",
    )
    gate = evaluate_v2_dataset_stage_gate(dataset, stage="v2_3_graph_conformance")
    if not gate.ready:
        raise ValueError(f"V2.3 Graph dataset StageGate 未通过：{gate.blockers}")

    compiler_result = compiler_runner.run_v2_2_compiler_benchmark(
        output / "compiler-input",
        manifest_path=manifest_path,
        benchmark_root=benchmark_root,
    )
    if not compiler_result.report.ready:
        raise ValueError("V2.2 Compiler 输入未通过，不得执行 V2.3 Graph。")
    input_intent_outcomes_sha256 = compiler_result.report.input_intent_outcomes_sha256
    input_compiler_outcomes_sha256 = compiler_result.report.outcomes_sha256
    development = tuple(
        sample
        for sample in dataset.manifest.split("development").samples
        if sample.dataset_role == "regression"
        and sample.source_suite_id == "png_to_shader_v1_m0"
    )
    validation = dataset.manifest.split("validation").samples
    restart_representative_case_ids = {
        split: next(
            sample.case_id
            for sample in samples
            if sample.topology == "solid"
            and sample.instance_count == 1
            and sample.hole_count == 0
        )
        for split, samples in (("development", development), ("validation", validation))
    }
    config_payload: dict[str, object] = {
        "schema_version": "v2_3_graph_benchmark_config_v2",
        "runner_version": RUNNER_VERSION,
        "execution_mode": EXECUTION_MODE,
        "model_calls_allowed": False,
        "model_call_budget": 0,
        "quality_claim": "graph_control_and_artifact_conformance_only",
        "gate_stage": "v2_3_graph_conformance",
        "manifest_id": gate.manifest_id,
        "dataset_version": gate.dataset_version,
        "manifest_sha256": gate.manifest_sha256,
        "taxonomy_sha256": gate.taxonomy_sha256,
        "input_intent_outcomes_sha256": input_intent_outcomes_sha256,
        "input_compiler_outcomes_sha256": input_compiler_outcomes_sha256,
        "input_compiler_config_sha256": compiler_result.config["config_sha256"],
        "input_compiler_report_sha256": canonical_sha256(
            compiler_result.report.model_dump(mode="python")
        ),
        "development_case_count": 10,
        "validation_case_count": 41,
        "seeds_per_feasible_hypothesis": 3,
        "production_admission_enabled": False,
        "langsmith_tracing_enabled": False,
        "release_held_out_accessed": False,
        "renderer_backend": "deterministic_reference_png_fixture_not_chromium",
        "restart_matrix_phases": (
            "measured",
            "interpreted",
            "seeding",
            "compiled",
            "rendered",
            "evaluated",
            "materialized",
            "selected",
        ),
        "restart_representatives_per_split": 1,
        "restart_representative_selection": (
            "first_manifest_order_solid_single_instance_zero_hole_per_split_v1"
        ),
        "restart_representative_case_ids": restart_representative_case_ids,
    }
    config_sha256 = canonical_sha256(config_payload)
    config = {**config_payload, "config_sha256": config_sha256}
    _write_json(output / "config.json", config)

    restart_representatives = {
        (split, case_id)
        for split, case_id in restart_representative_case_ids.items()
    }
    restart_phases = V2_3_RESTART_PHASES
    outcomes = tuple(
        _run_case(
            output=output,
            dataset=dataset,
            gate=gate,
            sample=sample,
            split=cast(Literal["development", "validation"], split),
            config=config,
            config_sha256=config_sha256,
            input_intent_outcomes_sha256=input_intent_outcomes_sha256,
            input_compiler_outcomes_sha256=input_compiler_outcomes_sha256,
            restart_phases=(
                restart_phases
                if (split, sample.case_id) in restart_representatives
                else ()
            ),
        )
        for split, samples in (("development", development), ("validation", validation))
        for sample in samples
    )
    report = evaluate_v2_3_graph_gate(
        dataset,
        gate,
        outcomes,
        config_sha256=config_sha256,
        input_intent_outcomes_sha256=input_intent_outcomes_sha256,
        input_compiler_outcomes_sha256=input_compiler_outcomes_sha256,
    )
    summary: dict[str, object] = {
        "schema_version": "v2_3_graph_benchmark_summary_v2",
        "runner_version": RUNNER_VERSION,
        "execution_mode": EXECUTION_MODE,
        "model_calls": report.model_calls,
        "production_admission_enabled": report.production_admission_enabled,
        "langsmith_tracing_enabled": False,
        "release_held_out_accessed": False,
        "renderer_backend": "deterministic_reference_png_fixture_not_chromium",
        "config_sha256": config_sha256,
        "input_intent_outcomes_sha256": input_intent_outcomes_sha256,
        "input_compiler_outcomes_sha256": input_compiler_outcomes_sha256,
        "ready": report.ready,
        "blockers": report.blockers,
        "case_count": len(outcomes),
        "success_count": sum(item.success for item in outcomes),
        "failure_count": sum(not item.success for item in outcomes),
        "restart_phase_recoveries": tuple(
            item.model_dump(mode="json")
            for item in report.restart_phase_recoveries
        ),
    }
    _write_json(
        output / "outcomes.json", [item.model_dump(mode="json") for item in outcomes]
    )
    _write_json(output / "report.json", report.model_dump(mode="json"))
    _write_json(output / "summary.json", summary)
    return V2_3GraphBenchmarkRun(
        output_dir=output,
        config=config,
        outcomes=outcomes,
        report=report,
        summary=summary,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行 V2.3 Graph conformance。")
    parser.add_argument("--output", required=True, help="必须尚不存在的输出目录。")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--benchmark-root", default=str(DEFAULT_BENCHMARK_ROOT))
    parser.add_argument("--execution-mode", default=EXECUTION_MODE)
    parser.add_argument("--allow-model-calls", action="store_true")
    parser.add_argument("--enable-real-model", action="store_true")
    parser.add_argument("--model-call-budget", type=int, default=0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """解析 CLI 并在门禁未通过时返回 2。."""
    args = _parser().parse_args(argv)
    result = run_v2_3_graph_benchmark(
        args.output,
        manifest_path=args.manifest,
        benchmark_root=args.benchmark_root,
        execution_mode=args.execution_mode,
        allow_model_calls=args.allow_model_calls,
        enable_real_model=args.enable_real_model,
        model_call_budget=args.model_call_budget,
    )
    sys.stdout.write(
        json.dumps(
            {
                "execution_mode": EXECUTION_MODE,
                "model_calls": result.report.model_calls,
                "output": str(result.output_dir),
                "ready": result.report.ready,
                "blockers": result.report.blockers,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n"
    )
    return 0 if result.report.ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
