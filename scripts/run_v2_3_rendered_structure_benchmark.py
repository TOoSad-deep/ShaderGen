"""运行 V2.3 strict actual-Chromium rendered-structure conformance。"""
# ruff: noqa: D415

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from pydantic import BaseModel

from agent.app.benchmarks.v2_rendered_gate_collector import (
    V2_3RenderedCaseCollectionIdentity,
    V2_3RenderedCaseCollectionResult,
    collect_v2_3_verified_rendered_case,
)
from agent.app.services.png_to_shader_v2 import (
    FixtureIntentInputFactory,
    FixtureIntentInputsV1,
    FixtureRendererFactory,
    PngToShaderV2RequestMetadata,
    PngToShaderV2ServiceConfig,
    create_png_to_shader_v2_development_service,
)
from agent.app.states.png_to_shader_v2_state import BudgetVectorV2, PngToShaderV2State
from agent.app.states.png_to_shader_v2_state_store import (
    LocalPngToShaderV2StateStore,
)
from shaderforge.analysis import TargetMeasurementsV2ArtifactBundle
from shaderforge.benchmark import (
    LoadedV2Dataset,
    V2_3ActualChromiumReplayRunner,
    V2_3RenderedGraphCaseOutcome,
    V2_3RenderedGraphGateReport,
    V2_3VerifiedRenderedCaseCapability,
    V2DatasetSample,
    V2DatasetStageGate,
    build_v2_3_rendered_threshold_policy,
    evaluate_v2_3_rendered_structure_gate,
    evaluate_v2_dataset_stage_gate,
    load_v2_dataset_manifest,
)
from shaderforge.compiler import DIAGNOSTIC_OWNERSHIP_POLICY_VERSION
from shaderforge.contracts import canonical_sha256
from shaderforge.evaluation import RENDERED_STRUCTURE_METRIC_VERSION
from shaderforge.intent import build_intent_variants
from shaderforge.rendering import PlaywrightWebGL1Renderer, RenderResult
from shaderforge.store import (
    ArtifactCatalog,
    ArtifactRefV2,
    ArtifactResolver,
    LocalArtifactCatalog,
    LocalArtifactStore,
)

try:
    intent_runner = importlib.import_module("scripts.run_v2_1_intent_benchmark")
    compiler_runner = importlib.import_module("scripts.run_v2_2_compiler_benchmark")
except ModuleNotFoundError:  # pragma: no cover - direct scripts/ invocation
    intent_runner = importlib.import_module("run_v2_1_intent_benchmark")
    compiler_runner = importlib.import_module("run_v2_2_compiler_benchmark")

RUNNER_VERSION: Literal["v2_3_rendered_structure_actual_chromium_runner_v3"] = (
    "v2_3_rendered_structure_actual_chromium_runner_v3"
)
EXECUTION_MODE: Literal["fixture/no-model"] = "fixture/no-model"
PROJECT_ID = "v2-3-rendered-structure-benchmark"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "benchmarks/png_to_shader_v2/dataset_manifest.v1.json"
DEFAULT_BENCHMARK_ROOT = ROOT / "benchmarks"
_SUITE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class V2_3RenderedStructureBenchmarkRun:
    """一次完整 visible actual-render suite 的冻结结果。"""

    output_dir: Path
    config: dict[str, object]
    outcomes: tuple[V2_3RenderedGraphCaseOutcome, ...]
    report: V2_3RenderedGraphGateReport
    summary: dict[str, object]


@dataclass(frozen=True)
class _CaseGraphCallCount:
    split: Literal["development", "validation"]
    case_id: str
    physical_call_count: int


class _FixtureFactory:
    """复用 V2.1 fixture policy，并记录 Graph 前测量得到的 hypothesis 分母。"""

    def __init__(
        self,
        *,
        dataset: LoadedV2Dataset,
        sample: V2DatasetSample,
        run_id: str,
    ) -> None:
        self._dataset = dataset
        self._sample = sample
        self._run_id = run_id
        self.expected_hypothesis_count: int | None = None
        self.source_hypothesis_count: int | None = None
        self.rejected_hypothesis_count: int | None = None
        self.rejection_reason_counts: tuple[tuple[str, int], ...] = ()

    def __call__(
        self,
        bundle: TargetMeasurementsV2ArtifactBundle,
        catalog: ArtifactCatalog,
    ) -> FixtureIntentInputsV1:
        request_ref = _put_json(
            catalog,
            run_id=self._run_id,
            kind="v2_3_rendered_structure_case_request",
            schema_version="v2_3_rendered_structure_case_request_v1",
            value={
                "schema_version": "v2_3_rendered_structure_case_request_v1",
                "case_id": self._sample.case_id,
                "topology": self._sample.topology,
                "instance_count": self._sample.instance_count,
                "hole_count": self._sample.hole_count,
                "required_layers": self._sample.required_layers,
            },
        )
        interpretation, context = intent_runner._fixture_interpretation(  # noqa: SLF001
            self._dataset, self._sample, bundle.measurements_ref
        )
        # 可行分母必须与 Graph 的唯一入口完全一致。先以 request evidence
        # 构造等价 provisional constraints，只用于得到 hard-constraint partition；
        # 再把 partition 计数冻结进 policy，并用最终 policy ref 重建正式输入。
        provisional_constraints = intent_runner._build_constraints(  # noqa: SLF001
            self._sample, bundle, request_ref, request_ref
        )
        provisional = build_intent_variants(
            bundle.measurements,
            interpretation,
            provisional_constraints,
            context,
        )
        source_count = len(provisional.source_hypotheses)
        feasible_count = len(provisional.variants)
        rejected_count = len(provisional.rejections)
        if feasible_count < 1:
            raise ValueError(
                "fixture hard constraints 过滤后必须至少有一个可行 Intent。"
            )
        rejection_reason_counts = tuple(
            sorted(
                Counter(
                    reason
                    for rejection in provisional.rejections
                    for reason in rejection.reason_codes
                ).items()
            )
        )
        policy_ref = _put_json(
            catalog,
            run_id=self._run_id,
            kind="v2_3_rendered_structure_fixture_policy",
            schema_version="v2_3_rendered_structure_fixture_policy_v2",
            value={
                "schema_version": "v2_3_rendered_structure_fixture_policy_v2",
                "fixture_policy": "reuse_v2_1_conformance_inputs_v1",
                "hypothesis_denominator_policy": (
                    "build_intent_variants_feasible_variants_v1"
                ),
                "source_hypothesis_count": source_count,
                "feasible_variant_count": feasible_count,
                "rejected_hypothesis_count": rejected_count,
                "rejection_reason_counts": rejection_reason_counts,
                "quality_claim": "not_vlm_quality",
                "production_admission_enabled": False,
            },
        )
        constraints = intent_runner._build_constraints(  # noqa: SLF001
            self._sample, bundle, request_ref, policy_ref
        )
        final_partition = build_intent_variants(
            bundle.measurements,
            interpretation,
            constraints,
            context,
        )
        final_rejections = tuple(
            (
                item.target_hypothesis_id,
                item.target_hypothesis_hash,
                item.reason_codes,
            )
            for item in final_partition.rejections
        )
        provisional_rejections = tuple(
            (
                item.target_hypothesis_id,
                item.target_hypothesis_hash,
                item.reason_codes,
            )
            for item in provisional.rejections
        )
        if (
            final_partition.source_hypotheses != provisional.source_hypotheses
            or tuple(
                (item.target_hypothesis_id, item.target_hypothesis_hash)
                for item in final_partition.variants
            )
            != tuple(
                (item.target_hypothesis_id, item.target_hypothesis_hash)
                for item in provisional.variants
            )
            or final_rejections != provisional_rejections
        ):
            raise ValueError(
                "fixture policy evidence 改变了 hard-constraint partition。"
            )
        self.source_hypothesis_count = source_count
        self.expected_hypothesis_count = len(final_partition.variants)
        self.rejected_hypothesis_count = rejected_count
        self.rejection_reason_counts = rejection_reason_counts
        return FixtureIntentInputsV1(
            request_constraint_set=constraints,
            visual_interpretation=interpretation,
            intent_context=context,
        )


class _SharedRendererLease:
    """Graph 每次调用可关闭的借用句柄；底层 Chromium 只由 suite 关闭。"""

    def __init__(self, owner: _SharedSuiteRenderer) -> None:
        self._owner = owner
        self._closed = False

    async def render(
        self, fragment_source: str, width: int, height: int
    ) -> RenderResult:
        if self._closed:
            raise RuntimeError("Renderer lease 已关闭。")
        return await self._owner.render(fragment_source, width, height)

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._owner.closed_lease_count += 1


class _SharedSuiteRenderer:
    """visible 51 case 共用的唯一 concrete Playwright renderer。"""

    def __init__(self) -> None:
        self.renderer = PlaywrightWebGL1Renderer()
        self.started = False
        self.startup_error_type: str | None = None
        self.physical_call_count = 0
        self.lease_count = 0
        self.closed_lease_count = 0

    async def start(self) -> None:
        try:
            await self.renderer.__aenter__()
        except Exception as exc:  # suite 继续，为 51 个 case 保留失败分母
            self.startup_error_type = type(exc).__name__
        else:
            self.started = True

    def lease(
        self,
        _state: PngToShaderV2State,
        _normalized_reference_png: bytes,
    ) -> _SharedRendererLease:
        self.lease_count += 1
        return _SharedRendererLease(self)

    async def render(
        self, fragment_source: str, width: int, height: int
    ) -> RenderResult:
        self.physical_call_count += 1
        if not self.started:
            raise RuntimeError(
                f"suite_renderer_startup_failed:{self.startup_error_type or 'unknown'}"
            )
        return await self.renderer.render(fragment_source, width, height)

    async def close(self) -> None:
        await self.renderer.close()
        self.started = False


class _UnavailableResolver:
    """Service 尚未登记 Artifact run 时供 collector 形成失败 capability。"""

    def resolve(self, artifact_id: str) -> ArtifactRefV2:
        raise FileNotFoundError(f"Artifact run unavailable: {artifact_id}")

    def read_bytes(self, artifact_id: str) -> bytes:
        raise FileNotFoundError(f"Artifact run unavailable: {artifact_id}")


def _json_default(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"{type(value).__name__} 不能编码为 benchmark JSON。")


def _stable_json_bytes(value: object, *, pretty: bool = False) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
            default=_json_default,
        )
        + "\n"
    ).encode("utf-8")


def _write_json_atomic(path: Path, value: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _stable_json_bytes(value, pretty=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return sha256(payload).hexdigest()


def _put_json(
    catalog: ArtifactCatalog,
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
        data=_stable_json_bytes(value),
    )


def _validate_case_budget(value: BudgetVectorV2) -> None:
    if any((value.model_calls, value.model_tokens, value.cost_usd_micros)):
        raise ValueError("fixture/no-model 的 model/token/cost 预算必须全部为 0。")
    if any(
        item <= 0
        for item in (
            value.wall_time_ms,
            value.render_calls,
            value.candidate_attempts,
            value.artifact_bytes,
        )
    ):
        raise ValueError("wall/render/candidate/artifact 必须是显式有限正预算。")


def _freeze_sources(
    dataset: LoadedV2Dataset,
    samples: Sequence[V2DatasetSample],
) -> dict[str, bytes]:
    frozen: dict[str, bytes] = {}
    for sample in samples:
        payload = dataset.resolve_image(sample).read_bytes()
        if sha256(payload).hexdigest() != sample.sha256:
            raise ValueError(f"{sample.case_id} source SHA-256 在运行前漂移。")
        frozen[sample.case_id] = payload
    return frozen


def _source_license(dataset: LoadedV2Dataset, sample: V2DatasetSample) -> str:
    matches = tuple(
        item.license_id
        for item in dataset.manifest.source_records
        if item.source_suite_id == sample.source_suite_id
    )
    if len(matches) != 1:
        raise ValueError(f"{sample.case_id} source/许可记录不闭合。")
    return matches[0]


def _case_run_id(
    suite_run_id: str,
    split: Literal["development", "validation"],
    case_id: str,
) -> str:
    run_hash = sha256(f"{suite_run_id}\0{split}\0{case_id}".encode()).hexdigest()[:24]
    return f"v2-actual-{run_hash}"


def _open_case_resolver(artifact_root: Path, run_id: str) -> ArtifactResolver:
    try:
        run_store = LocalArtifactStore(artifact_root).resolve_run(run_id)
    except (FileNotFoundError, OSError, TypeError, ValueError):
        return _UnavailableResolver()
    return LocalArtifactCatalog(run_store, run_id=run_id)


async def _run_case_async(
    *,
    case_root: Path,
    dataset: LoadedV2Dataset,
    sample: V2DatasetSample,
    split: Literal["development", "validation"],
    source_bytes: bytes,
    suite_run_id: str,
    run_id: str,
    case_budget: BudgetVectorV2,
    graph_renderer: _SharedSuiteRenderer,
    replay_runner: V2_3ActualChromiumReplayRunner,
    gate: V2DatasetStageGate,
    config_sha256: str,
    threshold_policy_hash: str,
    input_intent_outcomes_sha256: str,
    input_compiler_outcomes_sha256: str,
) -> V2_3RenderedCaseCollectionResult:
    case_root.mkdir(parents=True, exist_ok=False)
    artifact_root = case_root / "artifact-store"
    state_root = case_root / "state-store"
    fixture_factory = _FixtureFactory(dataset=dataset, sample=sample, run_id=run_id)
    service_error_type: str | None = None
    try:
        service = create_png_to_shader_v2_development_service(
            artifact_root=artifact_root,
            state_root=state_root,
            fixture_input_factory=cast(FixtureIntentInputFactory, fixture_factory),
            renderer_factory=cast(FixtureRendererFactory, graph_renderer.lease),
        )
        await service.invoke(
            project_id=PROJECT_ID,
            run_id=run_id,
            source_bytes=source_bytes,
            request_metadata=PngToShaderV2RequestMetadata(
                request_id=f"{suite_run_id}-{split}-{sample.case_id}",
                expected_source_sha256=sample.sha256,
                source_label=f"{split}/{sample.case_id}",
                source_license=_source_license(dataset, sample),
            ),
            config=PngToShaderV2ServiceConfig(
                execution_mode=EXECUTION_MODE,
                allow_model_calls=False,
                real_provider_enabled=False,
                production_admission_enabled=False,
                budget_limits=case_budget,
            ),
        )
    except Exception as exc:  # case 执行失败保留分母；不吞 KeyboardInterrupt/SystemExit
        service_error_type = type(exc).__name__

    resolver = _open_case_resolver(artifact_root, run_id)
    expected_hypotheses = fixture_factory.expected_hypothesis_count or 1
    collection = await collect_v2_3_verified_rendered_case(
        state_store=LocalPngToShaderV2StateStore(state_root),
        run_id=run_id,
        resolver=resolver,
        identity=V2_3RenderedCaseCollectionIdentity(
            manifest_id=gate.manifest_id,
            dataset_version=gate.dataset_version,
            manifest_sha256=gate.manifest_sha256,
            taxonomy_sha256=gate.taxonomy_sha256,
            config_sha256=config_sha256,
            threshold_policy_hash=threshold_policy_hash,
            input_intent_outcomes_sha256=input_intent_outcomes_sha256,
            input_compiler_outcomes_sha256=input_compiler_outcomes_sha256,
            split=split,
            case_id=sample.case_id,
            source_image_sha256=sample.sha256,
            expected_hypothesis_count=expected_hypotheses,
        ),
        replay_runner=replay_runner,
    )
    receipt_payload = {
        "schema_version": "v2_3_actual_chromium_case_receipt_set_v1",
        "suite_run_id": suite_run_id,
        "split": split,
        "case_id": sample.case_id,
        "run_id": run_id,
        "service_error_type": service_error_type,
        "receipts": tuple(item.model_dump(mode="json") for item in collection.receipts),
    }
    _write_json_atomic(case_root / "actual-replay-receipts.json", receipt_payload)
    _write_json_atomic(
        case_root / "outcome.json",
        collection.capability.outcome.model_dump(mode="json"),
    )
    return collection


async def _execute_visible_suite(
    *,
    output: Path,
    dataset: LoadedV2Dataset,
    gate: V2DatasetStageGate,
    samples: tuple[tuple[Literal["development", "validation"], V2DatasetSample], ...],
    frozen_sources: dict[str, bytes],
    suite_run_id: str,
    case_budget: BudgetVectorV2,
    config_sha256: str,
    threshold_policy_hash: str,
    input_intent_outcomes_sha256: str,
    input_compiler_outcomes_sha256: str,
) -> tuple[
    tuple[V2_3RenderedCaseCollectionResult, ...],
    _SharedSuiteRenderer,
    str | None,
    tuple[_CaseGraphCallCount, ...],
]:
    graph_renderer = _SharedSuiteRenderer()
    replay_runner = V2_3ActualChromiumReplayRunner()
    await graph_renderer.start()
    replay_startup_error_type: str | None = None
    replay_entered = False
    try:
        try:
            await replay_runner.__aenter__()
        except Exception as exc:  # collector 为全部 case 签发失败 capability
            replay_startup_error_type = type(exc).__name__
        else:
            replay_entered = True
        results: list[V2_3RenderedCaseCollectionResult] = []
        graph_call_counts: list[_CaseGraphCallCount] = []
        for split, sample in samples:
            calls_before = graph_renderer.physical_call_count
            results.append(
                await _run_case_async(
                    case_root=output / "cases" / split / sample.case_id,
                    dataset=dataset,
                    sample=sample,
                    split=split,
                    source_bytes=frozen_sources[sample.case_id],
                    suite_run_id=suite_run_id,
                    run_id=_case_run_id(suite_run_id, split, sample.case_id),
                    case_budget=case_budget,
                    graph_renderer=graph_renderer,
                    replay_runner=replay_runner,
                    gate=gate,
                    config_sha256=config_sha256,
                    threshold_policy_hash=threshold_policy_hash,
                    input_intent_outcomes_sha256=input_intent_outcomes_sha256,
                    input_compiler_outcomes_sha256=input_compiler_outcomes_sha256,
                )
            )
            graph_call_counts.append(
                _CaseGraphCallCount(
                    split=split,
                    case_id=sample.case_id,
                    physical_call_count=(
                        graph_renderer.physical_call_count - calls_before
                    ),
                )
            )
        return (
            tuple(results),
            graph_renderer,
            replay_startup_error_type,
            tuple(graph_call_counts),
        )
    finally:
        if replay_entered:
            await replay_runner.__aexit__()
        else:
            # __aenter__ 失败后仍让 concrete runner 清理可能的部分资源。
            await replay_runner.__aexit__()
        await graph_renderer.close()


def _validate_render_call_accounting(
    *,
    ordered_collections: tuple[V2_3RenderedCaseCollectionResult, ...],
    graph_call_counts: tuple[_CaseGraphCallCount, ...],
    graph_observed_total: int,
    case_graph_limit: int,
    case_replay_limit: int,
) -> tuple[int, int, int]:
    """闭合 Graph 实测调用与 replay 成功 receipt 调用，并复验硬上界。"""
    if len(ordered_collections) != len(graph_call_counts):
        raise ValueError("Graph/replay case 调用计数分母不一致。")
    indexed_graph = {
        (item.split, item.case_id): item.physical_call_count
        for item in graph_call_counts
    }
    if len(indexed_graph) != len(graph_call_counts):
        raise ValueError("Graph case 调用计数存在重复身份。")
    if any(value > case_graph_limit for value in indexed_graph.values()):
        raise ValueError("单 case Graph physical render 调用超过冻结硬上限。")
    graph_total = sum(indexed_graph.values())
    if graph_total != graph_observed_total:
        raise ValueError("逐 case Graph physical calls 与 suite worker 实测不闭合。")

    replay_receipted_total = 0
    for collection in ordered_collections:
        outcome = collection.capability.outcome
        key = cast(
            tuple[Literal["development", "validation"], str],
            (outcome.split, outcome.case_id),
        )
        if key not in indexed_graph:
            raise ValueError("Graph/replay case 身份集合不闭合。")
        receipted = sum(len(item.item_receipts) for item in collection.receipts)
        if receipted > case_replay_limit:
            raise ValueError("单 case replay receipt render 调用超过冻结硬上限。")
        if outcome.success and receipted != outcome.nominal_render_request_count:
            raise ValueError("成功 case replay receipts 未闭合完整 RenderPlan 分母。")
        replay_receipted_total += receipted
    suite_graph_limit = case_graph_limit * len(graph_call_counts)
    suite_replay_limit = case_replay_limit * len(ordered_collections)
    if graph_total > suite_graph_limit or replay_receipted_total > suite_replay_limit:
        raise ValueError("suite Graph/replay render 调用超过冻结硬上限。")
    return graph_total, replay_receipted_total, suite_replay_limit


def run_v2_3_rendered_structure_benchmark(
    output_dir: str | Path,
    *,
    suite_run_id: str,
    case_budget: BudgetVectorV2,
    manifest_path: str | Path = DEFAULT_MANIFEST,
    benchmark_root: str | Path = DEFAULT_BENCHMARK_ROOT,
    execution_mode: str = EXECUTION_MODE,
    allow_model_calls: bool = False,
    enable_real_model: bool = False,
) -> V2_3RenderedStructureBenchmarkRun:
    """执行 visible 10+41 的 Graph actual render、独立 replay 与正式门禁。"""
    output = Path(output_dir)
    if output.exists():
        raise FileExistsError(f"strict rendered benchmark 输出目录已存在：{output}")
    if not _SUITE_ID.fullmatch(suite_run_id):
        raise ValueError("suite_run_id 必须显式提供且只含安全字符。")
    if execution_mode != EXECUTION_MODE:
        raise ValueError("strict rendered benchmark 固定为 fixture/no-model。")
    if allow_model_calls or enable_real_model:
        raise ValueError("strict rendered benchmark 禁止启用任何模型开关。")
    _validate_case_budget(case_budget)

    dataset = load_v2_dataset_manifest(
        manifest_path,
        benchmark_root=benchmark_root,
        gate_stage="v2_3_graph_conformance",
    )
    gate = evaluate_v2_dataset_stage_gate(dataset, stage="v2_3_graph_conformance")
    if not gate.ready:
        raise ValueError(f"V2.3 visible StageGate 未通过：{gate.blockers}")
    development = tuple(
        sample
        for sample in dataset.manifest.split("development").samples
        if sample.dataset_role == "regression"
        and sample.source_suite_id == "png_to_shader_v1_m0"
    )
    validation = dataset.manifest.split("validation").samples
    if len(development) != 10 or len(validation) != 41:
        raise ValueError(
            "strict rendered benchmark 固定要求 development 10 + validation 41。"
        )
    ordered_samples = tuple(
        (cast(Literal["development", "validation"], split), sample)
        for split, split_samples in (
            ("development", development),
            ("validation", validation),
        )
        for sample in split_samples
    )
    frozen_sources = _freeze_sources(
        dataset, tuple(sample for _split, sample in ordered_samples)
    )

    output.mkdir(parents=True, exist_ok=False)
    compiler_result = compiler_runner.run_v2_2_compiler_benchmark(
        output / "compiler-input",
        manifest_path=manifest_path,
        benchmark_root=benchmark_root,
    )
    if not compiler_result.report.ready:
        raise ValueError("V2.2 Compiler 输入未通过，不得执行 strict rendered suite。")
    input_intent_outcomes_sha256 = compiler_result.report.input_intent_outcomes_sha256
    input_compiler_outcomes_sha256 = compiler_result.report.outcomes_sha256
    threshold_policy = build_v2_3_rendered_threshold_policy()
    suite_budget = BudgetVectorV2(
        **{
            name: getattr(case_budget, name) * 51
            for name in BudgetVectorV2.model_fields
        }
    )
    config_payload: dict[str, object] = {
        "schema_version": "v2_3_rendered_structure_benchmark_config_v3",
        "runner_version": RUNNER_VERSION,
        "suite_run_id": suite_run_id,
        "gate_stage": "v2_3_rendered_structure_conformance",
        "dataset_gate_stage": gate.stage,
        "execution_mode": EXECUTION_MODE,
        "allow_model_calls": False,
        "enable_real_model": False,
        "production_admission_enabled": False,
        "release_held_out_accessed": False,
        "renderer_backend": "chromium_webgl1_actual_v1",
        "graph_renderer_lifecycle": "one_concrete_renderer_per_suite_borrowed_leases_v1",
        "graph_canvas_lifecycle": "new_canvas_per_physical_call",
        "replay_backend": "sealed_v2_3_actual_chromium_replay_runner_v1",
        "diagnostic_ownership_policy_version": (
            DIAGNOSTIC_OWNERSHIP_POLICY_VERSION
        ),
        "rendered_structure_metric_version": RENDERED_STRUCTURE_METRIC_VERSION,
        "diagnostic_compilation_schema_version": "diagnostic_compilation_bundle_v3",
        "render_plan_schema_version": "renderer_plan_v3",
        "rendered_structure_evidence_schema_version": (
            "rendered_structure_evidence_v4"
        ),
        "rendered_structure_verification_schema_version": (
            "rendered_structure_verification_v4"
        ),
        "quality_claim": "fixture_intent_actual_render_structure_conformance_only",
        "hypothesis_denominator_policy": ("build_intent_variants_feasible_variants_v1"),
        "manifest_id": gate.manifest_id,
        "dataset_version": gate.dataset_version,
        "manifest_sha256": gate.manifest_sha256,
        "taxonomy_sha256": gate.taxonomy_sha256,
        "threshold_policy": threshold_policy,
        "input_intent_outcomes_sha256": input_intent_outcomes_sha256,
        "input_compiler_outcomes_sha256": input_compiler_outcomes_sha256,
        "input_compiler_config_sha256": compiler_result.config["config_sha256"],
        "input_compiler_report_sha256": canonical_sha256(
            compiler_result.report.model_dump(mode="python")
        ),
        "case_graph_budget": case_budget,
        "suite_graph_budget": suite_budget,
        "case_actual_replay_render_call_limit": case_budget.render_calls,
        "suite_actual_replay_render_call_limit": case_budget.render_calls * 51,
        "suite_total_actual_render_call_limit": case_budget.render_calls * 102,
        "development_case_count": len(development),
        "validation_case_count": len(validation),
    }
    config_sha256 = canonical_sha256(config_payload)
    config = {**config_payload, "config_sha256": config_sha256}
    file_hashes: dict[str, str] = {
        "config.json": _write_json_atomic(output / "config.json", config)
    }

    (
        collections,
        graph_renderer,
        replay_startup_error_type,
        graph_call_counts,
    ) = asyncio.run(
        _execute_visible_suite(
            output=output,
            dataset=dataset,
            gate=gate,
            samples=ordered_samples,
            frozen_sources=frozen_sources,
            suite_run_id=suite_run_id,
            case_budget=case_budget,
            config_sha256=config_sha256,
            threshold_policy_hash=threshold_policy.policy_hash,
            input_intent_outcomes_sha256=input_intent_outcomes_sha256,
            input_compiler_outcomes_sha256=input_compiler_outcomes_sha256,
        )
    )
    indexed_collections = {
        (item.capability.outcome.split, item.capability.outcome.case_id): item
        for item in collections
    }
    if len(indexed_collections) != len(collections):
        raise ValueError("strict collector 返回了重复 case capability。")
    ordered_collections = tuple(
        indexed_collections[key] for key in sorted(indexed_collections)
    )
    (
        graph_physical_render_call_count,
        replay_receipted_render_call_count,
        suite_replay_render_call_limit,
    ) = _validate_render_call_accounting(
        ordered_collections=ordered_collections,
        graph_call_counts=graph_call_counts,
        graph_observed_total=graph_renderer.physical_call_count,
        case_graph_limit=case_budget.render_calls,
        case_replay_limit=case_budget.render_calls,
    )
    capabilities: tuple[V2_3VerifiedRenderedCaseCapability, ...] = tuple(
        item.capability for item in ordered_collections
    )
    outcomes = tuple(item.outcome for item in capabilities)
    report = evaluate_v2_3_rendered_structure_gate(
        dataset,
        gate,
        capabilities,
        config_sha256=config_sha256,
        input_intent_outcomes_sha256=input_intent_outcomes_sha256,
        input_compiler_outcomes_sha256=input_compiler_outcomes_sha256,
        threshold_policy=threshold_policy,
    )
    if report.outcomes_sha256 != canonical_sha256(
        tuple(item.model_dump(mode="python") for item in outcomes)
    ):
        raise ValueError("ordered outcomes 与正式 Gate report hash 不闭合。")
    receipts_payload = {
        "schema_version": "v2_3_actual_chromium_suite_receipt_set_v1",
        "suite_run_id": suite_run_id,
        "cases": tuple(
            {
                "split": outcome.split,
                "case_id": outcome.case_id,
                "run_id": _case_run_id(
                    suite_run_id,
                    cast(Literal["development", "validation"], outcome.split),
                    outcome.case_id,
                ),
                "receipts": tuple(
                    receipt.model_dump(mode="json") for receipt in result.receipts
                ),
            }
            for result, outcome in zip(ordered_collections, outcomes, strict=True)
        ),
    }
    receipts_sha256 = canonical_sha256(receipts_payload)
    summary_payload: dict[str, object] = {
        "schema_version": "v2_3_rendered_structure_benchmark_summary_v2",
        "runner_version": RUNNER_VERSION,
        "suite_run_id": suite_run_id,
        "execution_mode": EXECUTION_MODE,
        "config_sha256": config_sha256,
        "input_intent_outcomes_sha256": input_intent_outcomes_sha256,
        "input_compiler_outcomes_sha256": input_compiler_outcomes_sha256,
        "outcomes_sha256": report.outcomes_sha256,
        "actual_replay_receipts_sha256": receipts_sha256,
        "report_sha256": report.record_hash,
        "case_count": len(outcomes),
        "success_count": sum(item.success for item in outcomes),
        "failure_count": sum(not item.success for item in outcomes),
        "graph_physical_render_call_count": graph_physical_render_call_count,
        "graph_render_call_limit": case_budget.render_calls * len(outcomes),
        "actual_replay_receipted_render_call_count": (
            replay_receipted_render_call_count
        ),
        "actual_replay_render_call_limit": suite_replay_render_call_limit,
        "actual_replay_unreceipted_call_upper_bound": (
            suite_replay_render_call_limit - replay_receipted_render_call_count
        ),
        "graph_renderer_lease_count": graph_renderer.lease_count,
        "graph_renderer_closed_lease_count": graph_renderer.closed_lease_count,
        "graph_renderer_startup_error_type": graph_renderer.startup_error_type,
        "replay_renderer_startup_error_type": replay_startup_error_type,
        "renderer_environment_hashes": report.renderer_environment_hashes,
        "model_calls": 0,
        "model_tokens": 0,
        "cost_usd_micros": 0,
        "production_admission_enabled": False,
        "release_held_out_accessed": False,
        "ready": report.ready,
        "blockers": report.blockers,
    }
    summary = {
        **summary_payload,
        "summary_sha256": canonical_sha256(summary_payload),
    }
    file_hashes.update(
        {
            "outcomes.json": _write_json_atomic(
                output / "outcomes.json",
                tuple(item.model_dump(mode="json") for item in outcomes),
            ),
            "actual-replay-receipts.json": _write_json_atomic(
                output / "actual-replay-receipts.json", receipts_payload
            ),
            "report.json": _write_json_atomic(
                output / "report.json", report.model_dump(mode="json")
            ),
            "summary.json": _write_json_atomic(output / "summary.json", summary),
        }
    )
    _write_json_atomic(
        output / "sha256s.json",
        {
            "schema_version": "v2_3_rendered_structure_file_hashes_v1",
            "suite_run_id": suite_run_id,
            "hash_algorithm": "sha256",
            "files": file_hashes,
        },
    )
    return V2_3RenderedStructureBenchmarkRun(
        output_dir=output,
        config=config,
        outcomes=outcomes,
        report=report,
        summary=summary,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="运行 V2.3 strict actual-Chromium rendered structure suite。"
    )
    parser.add_argument("--output", required=True, help="必须尚不存在的输出目录。")
    parser.add_argument("--suite-run-id", required=True)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--benchmark-root", default=str(DEFAULT_BENCHMARK_ROOT))
    parser.add_argument("--execution-mode", default=EXECUTION_MODE)
    parser.add_argument("--allow-model-calls", action="store_true")
    parser.add_argument("--enable-real-model", action="store_true")
    parser.add_argument("--case-wall-time-ms", type=int, default=300_000)
    parser.add_argument("--case-render-calls", type=int, default=512)
    parser.add_argument("--case-candidate-attempts", type=int, default=64)
    parser.add_argument("--case-artifact-bytes", type=int, default=536_870_912)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """解析 CLI；正式 gate 未 ready 时返回 2。"""
    args = _parser().parse_args(argv)
    result = run_v2_3_rendered_structure_benchmark(
        args.output,
        suite_run_id=args.suite_run_id,
        case_budget=BudgetVectorV2(
            wall_time_ms=args.case_wall_time_ms,
            model_calls=0,
            model_tokens=0,
            render_calls=args.case_render_calls,
            candidate_attempts=args.case_candidate_attempts,
            artifact_bytes=args.case_artifact_bytes,
            cost_usd_micros=0,
        ),
        manifest_path=args.manifest,
        benchmark_root=args.benchmark_root,
        execution_mode=args.execution_mode,
        allow_model_calls=args.allow_model_calls,
        enable_real_model=args.enable_real_model,
    )
    sys.stdout.write(
        json.dumps(
            {
                "suite_run_id": args.suite_run_id,
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
