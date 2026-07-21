"""运行不调用模型的 V2.2 三 Genome/Deterministic Compiler conformance。."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass, field
from enum import Enum
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Sequence, cast

from playwright.async_api import Error as PlaywrightError
from pydantic import BaseModel

from shaderforge.benchmark import (
    V2DatasetSample,
    V2DatasetStageGate,
    evaluate_v2_dataset_stage_gate,
    load_v2_dataset_manifest,
)
from shaderforge.benchmark.v2_2_compiler_gate import (
    V2_2CompilerCaseOutcome,
    V2_2CompilerGateReport,
    V2_2FailureCode,
    evaluate_v2_2_compiler_gate,
)
from shaderforge.compiler import (
    CompilationProduct,
    CompilerDefectError,
    compile_effect_genome,
    materialize_compilation,
)
from shaderforge.contracts import canonical_sha256
from shaderforge.genome import TypedEffectGenome
from shaderforge.intent import IntentIR
from shaderforge.rendering import PlaywrightWebGL1Renderer, RendererUnavailableError
from shaderforge.seeding import expand_seed_plans
from shaderforge.store import ArtifactRefV2, LocalArtifactCatalog, LocalArtifactStore
from shaderforge.validation import validate_shader

if TYPE_CHECKING:
    from scripts.run_v2_1_intent_benchmark import V2_1IntentBenchmarkRun

try:
    intent_runner = import_module("scripts.run_v2_1_intent_benchmark")
except ModuleNotFoundError:
    intent_runner = import_module("run_v2_1_intent_benchmark")

RUNNER_VERSION: Literal["v2_2_compiler_fixture_benchmark_v1"] = (
    "v2_2_compiler_fixture_benchmark_v1"
)
EXECUTION_MODE: Literal["fixture/no-model"] = "fixture/no-model"
RUN_ID = "v2-2-compiler-fixture-v1"
PROJECT_ID = "v2-2-compiler-benchmark"

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "benchmarks/png_to_shader_v2/dataset_manifest.v1.json"
DEFAULT_BENCHMARK_ROOT = ROOT / "benchmarks"

_FailureException = (OSError, ValueError, TypeError, KeyError, CompilerDefectError)


@dataclass(frozen=True)
class V2_2CompilerBenchmarkRun:
    """一次 fixture/no-model V2.2 benchmark 的本地结果。."""

    output_dir: Path
    config: dict[str, object]
    outcomes: tuple[V2_2CompilerCaseOutcome, ...]
    report: V2_2CompilerGateReport
    summary: dict[str, object]


@dataclass
class _CaseExecution:
    split: Literal["development", "validation"]
    sample: V2DatasetSample
    intent: IntentIR | None
    genome_hashes: tuple[str, ...] = ()
    distinct_structural_signatures: int = 0
    diversity_gate_passed: bool = False
    compile_success_count: int = 0
    static_success_count: int = 0
    webgl_success_count: int | None = None
    products: tuple[CompilationProduct, ...] = ()
    failure_code: V2_2FailureCode | None = None
    failure_ref: ArtifactRefV2 | None = None
    refs: dict[str, object] = field(default_factory=dict)


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"JSON 包含重复字段：{key}。")
        value[key] = item
    return value


def _load_json_object(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data, object_pairs_hook=_reject_duplicate_json_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Artifact 不是合法 UTF-8 JSON。") from exc
    if not isinstance(value, dict):
        raise ValueError("Artifact JSON 顶层必须是 object。")
    return value


def _json_default(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"{type(value).__name__} 不能编码为 benchmark JSON。")


def _stable_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _put_json(
    catalog: LocalArtifactCatalog,
    *,
    kind: str,
    schema_version: str,
    value: object,
) -> ArtifactRefV2:
    return catalog.put(
        run_id=RUN_ID,
        kind=kind,
        schema_version=schema_version,
        content_type="application/json",
        data=_stable_json_bytes(value),
    )


def _artifact_projection(ref: ArtifactRefV2 | None) -> object:
    return None if ref is None else asdict(ref)


def _parse_ref(value: object) -> ArtifactRefV2:
    if not isinstance(value, dict):
        raise ValueError("ArtifactRef 必须是 object。")
    return ArtifactRefV2(**value)


def _read_verified(
    catalog: LocalArtifactCatalog,
    ref: ArtifactRefV2,
) -> bytes:
    if catalog.resolve(ref.artifact_id) != ref:
        raise ValueError("ArtifactRef 与输入 Catalog manifest 不一致。")
    return catalog.read_bytes(ref.artifact_id)


def _load_frozen_intents(
    result: V2_1IntentBenchmarkRun,
) -> dict[tuple[str, str], tuple[IntentIR, ArtifactRefV2, bytes]]:
    """从 V2.1 内容寻址产物恢复 Intent，不从图片或标签重新推断。."""
    store = LocalArtifactStore(result.output_dir / "artifact-store")
    catalog = LocalArtifactCatalog(
        store.resolve_run(intent_runner.RUN_ID),
        run_id=intent_runner.RUN_ID,
    )
    valid_outcomes = {
        (item.split, item.case_id): item
        for item in result.outcomes
        if item.intent_valid
    }
    loaded: dict[tuple[str, str], tuple[IntentIR, ArtifactRefV2, bytes]] = {}
    raw_case_refs = result.summary.get("case_record_refs")
    if not isinstance(raw_case_refs, tuple):
        raise ValueError("V2.1 summary case_record_refs 无效。")
    for raw_case_ref in raw_case_refs:
        case_ref = _parse_ref(raw_case_ref)
        case_record = _load_json_object(_read_verified(catalog, case_ref))
        split = case_record.get("split")
        case_id = case_record.get("case_id")
        if split not in {"development", "validation"} or not isinstance(case_id, str):
            raise ValueError("V2.1 case record identity 无效。")
        key = (split, case_id)
        refs = case_record.get("refs")
        if not isinstance(refs, dict):
            raise ValueError("V2.1 case record refs 无效。")
        raw_intent_ref = refs.get("intent")
        if key not in valid_outcomes:
            if raw_intent_ref is not None:
                raise ValueError("非法 V2.1 outcome 不得携带 Intent artifact。")
            continue
        intent_ref = _parse_ref(raw_intent_ref)
        if (
            intent_ref.kind != "intent_ir"
            or intent_ref.schema_version != "intent_v3"
            or intent_ref.content_type != "application/json"
        ):
            raise ValueError("V2.1 Intent ArtifactRef 元数据无效。")
        intent_bytes = _read_verified(catalog, intent_ref)
        intent = IntentIR.model_validate_json(intent_bytes, strict=True)
        if key in loaded:
            raise ValueError(f"V2.1 Intent 重复：{split}/{case_id}。")
        loaded[key] = (intent, intent_ref, intent_bytes)
    if set(loaded) != set(valid_outcomes):
        raise ValueError("V2.1 Intent Artifact 与合法 outcome 集不闭合。")
    return loaded


def _seed_for_case(split: str, case_id: str, intent: IntentIR) -> int:
    digest = canonical_sha256(
        {
            "policy": "v2_2_case_seed_v1",
            "split": split,
            "case_id": case_id,
            "intent_id": intent.intent_id,
            "target_hypothesis_hash": intent.target_hypothesis_hash,
        }
    )
    return int(digest[:16], 16) % (9_223_372_036_854_775_805 + 1)


def _failure_code_for_phase(phase: str) -> V2_2FailureCode:
    mapping: dict[str, V2_2FailureCode] = {
        "input_intent": "input_intent_unavailable",
        "seed_expansion": "seed_expansion_failed",
        "typed_genome": "typed_genome_invalid",
        "deterministic_compile": "deterministic_compile_failed",
        "static_validation": "static_validation_failed",
    }
    return mapping[phase]


def _record_failure(
    execution: _CaseExecution,
    *,
    catalog: LocalArtifactCatalog,
    phase: str,
    failure_code: V2_2FailureCode,
    error: BaseException | None = None,
) -> None:
    execution.failure_code = failure_code
    execution.failure_ref = _put_json(
        catalog,
        kind="v2_2_compiler_case_failure",
        schema_version="v2_2_compiler_case_failure_v1",
        value={
            "schema_version": "v2_2_compiler_case_failure_v1",
            "execution_mode": EXECUTION_MODE,
            "case_id": execution.sample.case_id,
            "split": execution.split,
            "phase": phase,
            "failure_code": failure_code,
            "error_type": None if error is None else type(error).__name__,
            "message": None if error is None else str(error),
        },
    )


def _execute_case_static(
    execution: _CaseExecution,
    *,
    catalog: LocalArtifactCatalog,
    intent_bytes: bytes | None,
) -> None:
    """生成三 Genome、重复编译并执行静态 Validator。."""
    if execution.intent is None or intent_bytes is None:
        _record_failure(
            execution,
            catalog=catalog,
            phase="input_intent",
            failure_code="input_intent_unavailable",
        )
        return
    execution.refs["intent"] = _artifact_projection(
        catalog.put(
            run_id=RUN_ID,
            kind="intent_ir",
            schema_version="intent_v3",
            content_type="application/json",
            data=intent_bytes,
        )
    )

    phase = "seed_expansion"
    try:
        expansion = expand_seed_plans(
            execution.intent,
            random_seed=_seed_for_case(
                execution.split, execution.sample.case_id, execution.intent
            ),
        )
        execution.genome_hashes = tuple(
            item.genome_hashes.semantic_genome_hash for item in expansion.expanded_seeds
        )
        execution.distinct_structural_signatures = (
            expansion.diversity.distinct_structural_signatures
        )
        execution.diversity_gate_passed = expansion.diversity.gate_passed
        execution.refs["seed_expansion"] = _artifact_projection(
            _put_json(
                catalog,
                kind="seed_expansion_result",
                schema_version="seed_expansion_result_v2",
                value=expansion,
            )
        )
        if len(set(execution.genome_hashes)) != 3:
            _record_failure(
                execution,
                catalog=catalog,
                phase="seed_diversity",
                failure_code="semantic_genome_hash_not_unique",
            )
            return
        if not expansion.diversity.gate_passed:
            _record_failure(
                execution,
                catalog=catalog,
                phase="seed_diversity",
                failure_code="structural_diversity_failed",
            )
            return

        phase = "typed_genome"
        genomes = tuple(
            TypedEffectGenome.model_validate_json(
                item.genome.model_dump_json(), strict=True
            )
            for item in expansion.expanded_seeds
        )
        execution.refs["genomes"] = tuple(
            _artifact_projection(
                _put_json(
                    catalog,
                    kind="typed_effect_genome",
                    schema_version="typed_effect_genome_v2",
                    value=genome,
                )
            )
            for genome in genomes
        )

        products: list[CompilationProduct] = []
        bundles: list[object] = []
        for genome in genomes:
            phase = "deterministic_compile"
            first = compile_effect_genome(genome)
            second = compile_effect_genome(genome)
            if first != second:
                _record_failure(
                    execution,
                    catalog=catalog,
                    phase=phase,
                    failure_code="deterministic_compile_mismatch",
                )
                return
            execution.compile_success_count += 1
            phase = "static_validation"
            validation = validate_shader(first.glsl_source)
            if not validation.valid:
                _record_failure(
                    execution,
                    catalog=catalog,
                    phase=phase,
                    failure_code="static_validation_failed",
                )
                return
            execution.static_success_count += 1
            products.append(first)
            bundles.append(
                _artifact_projection(
                    _put_json(
                        catalog,
                        kind="compilation_bundle",
                        schema_version="compilation_bundle_v1",
                        value=materialize_compilation(
                            first, catalog=catalog, run_id=RUN_ID
                        ),
                    )
                )
            )
        execution.products = tuple(products)
        execution.refs["compilation_bundles"] = tuple(bundles)
    except _FailureException as exc:
        _record_failure(
            execution,
            catalog=catalog,
            phase=phase,
            failure_code=_failure_code_for_phase(phase),
            error=exc,
        )


async def _execute_webgl(
    executions: Sequence[_CaseExecution],
    *,
    catalog: LocalArtifactCatalog,
) -> None:
    """显式请求时运行全量 WebGL；否则本函数不会被调用。."""
    pending = [item for item in executions if item.failure_code is None]
    try:
        async with PlaywrightWebGL1Renderer() as renderer:
            for execution in pending:
                execution.webgl_success_count = 0
                render_refs: list[object] = []
                for product in execution.products:
                    try:
                        width, height = cast(
                            IntentIR, execution.intent
                        ).canvas.image_size
                        render = await renderer.render(
                            product.glsl_source,
                            width,
                            height,
                        )
                    except RendererUnavailableError as exc:
                        _record_failure(
                            execution,
                            catalog=catalog,
                            phase="webgl",
                            failure_code="webgl_renderer_unavailable",
                            error=exc,
                        )
                        break
                    image_ref = None
                    if render.image_bytes is not None:
                        image_ref = catalog.put(
                            run_id=RUN_ID,
                            kind="webgl_render_png",
                            schema_version="webgl_render_png_v1",
                            content_type="image/png",
                            data=render.image_bytes,
                        )
                    render_refs.append(
                        _artifact_projection(
                            _put_json(
                                catalog,
                                kind="webgl_render_result",
                                schema_version="webgl_render_result_v1",
                                value={
                                    "schema_version": "webgl_render_result_v1",
                                    "semantic_genome_hash": (
                                        product.semantic_genome_hash
                                    ),
                                    "result": render.to_dict(),
                                    "image_ref": _artifact_projection(image_ref),
                                },
                            )
                        )
                    )
                    if not render.success:
                        _record_failure(
                            execution,
                            catalog=catalog,
                            phase="webgl",
                            failure_code="webgl_compile_or_draw_failed",
                        )
                        break
                    execution.webgl_success_count += 1
                execution.refs["webgl_results"] = tuple(render_refs)
    except (OSError, PlaywrightError, RendererUnavailableError) as exc:
        for execution in pending:
            if execution.failure_code is None:
                execution.webgl_success_count = 0
                _record_failure(
                    execution,
                    catalog=catalog,
                    phase="webgl",
                    failure_code="webgl_renderer_unavailable",
                    error=exc,
                )


def _outcome(
    execution: _CaseExecution,
    *,
    gate: V2DatasetStageGate,
    config_sha256: str,
    input_intent_outcomes_sha256: str,
    with_webgl: bool,
) -> V2_2CompilerCaseOutcome:
    webgl_count = (execution.webgl_success_count or 0) if with_webgl else None
    success = (
        execution.failure_code is None
        and len(execution.genome_hashes) == 3
        and len(set(execution.genome_hashes)) == 3
        and execution.distinct_structural_signatures >= 2
        and execution.diversity_gate_passed
        and execution.compile_success_count == 3
        and execution.static_success_count == 3
        and (webgl_count == 3 if with_webgl else True)
    )
    return V2_2CompilerCaseOutcome(
        manifest_id=gate.manifest_id,
        dataset_version=gate.dataset_version,
        manifest_sha256=gate.manifest_sha256,
        taxonomy_sha256=gate.taxonomy_sha256,
        config_sha256=config_sha256,
        input_intent_outcomes_sha256=input_intent_outcomes_sha256,
        split=execution.split,
        case_id=execution.sample.case_id,
        success=success,
        genome_count=len(execution.genome_hashes),
        semantic_genome_hashes=execution.genome_hashes,
        distinct_structural_signatures=execution.distinct_structural_signatures,
        diversity_gate_passed=execution.diversity_gate_passed,
        deterministic_compile_success_count=execution.compile_success_count,
        static_validation_success_count=execution.static_success_count,
        webgl_requested=with_webgl,
        webgl_success_count=webgl_count,
        failure_code=execution.failure_code,
    )


def run_v2_2_compiler_benchmark(
    output_dir: str | Path,
    *,
    manifest_path: str | Path = DEFAULT_MANIFEST,
    benchmark_root: str | Path = DEFAULT_BENCHMARK_ROOT,
    with_webgl: bool = False,
) -> V2_2CompilerBenchmarkRun:
    """运行冻结的 51 Intent conformance；绝不读取 release-held-out。."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    dataset = load_v2_dataset_manifest(
        manifest_path,
        benchmark_root=benchmark_root,
        gate_stage="v2_2_genome_compiler",
    )
    gate = evaluate_v2_dataset_stage_gate(dataset, stage="v2_2_genome_compiler")
    if not gate.ready:
        raise ValueError(f"V2.2 dataset stage gate 未通过：{gate.blockers}")

    intent_result = intent_runner.run_v2_1_intent_benchmark(
        output / "intent-input",
        manifest_path=manifest_path,
        benchmark_root=benchmark_root,
    )
    if (
        intent_result.report.manifest_sha256 != gate.manifest_sha256
        or intent_result.report.taxonomy_sha256 != gate.taxonomy_sha256
        or intent_result.report.manifest_id != gate.manifest_id
        or intent_result.report.dataset_version != gate.dataset_version
    ):
        raise ValueError("V2.1 Intent 输入与 V2.2 StageGate 身份不一致。")
    loaded_intents = _load_frozen_intents(intent_result)
    input_outcomes_sha256 = intent_result.report.outcomes_sha256
    input_config_sha256 = cast(str, intent_result.config["config_sha256"])
    config_payload: dict[str, object] = {
        "schema_version": "v2_2_compiler_benchmark_config_v1",
        "runner_version": RUNNER_VERSION,
        "execution_mode": EXECUTION_MODE,
        "model_calls_allowed": False,
        "model_call_budget": 0,
        "quality_claim": (
            "conformance_static_plus_webgl" if with_webgl else "conformance_static_only"
        ),
        "run_id": RUN_ID,
        "gate_stage": "v2_2_genome_compiler",
        "manifest_id": gate.manifest_id,
        "dataset_version": gate.dataset_version,
        "manifest_sha256": gate.manifest_sha256,
        "taxonomy_sha256": gate.taxonomy_sha256,
        "input_intent_config_sha256": input_config_sha256,
        "input_intent_outcomes_sha256": input_outcomes_sha256,
        "input_intent_report_sha256": canonical_sha256(
            intent_result.report.model_dump(mode="python")
        ),
        "development_case_count": 10,
        "validation_case_count": 41,
        "genomes_per_intent": 3,
        "webgl_requested": with_webgl,
    }
    config_sha256 = canonical_sha256(config_payload)
    config = {**config_payload, "config_sha256": config_sha256}
    _write_json(output / "config.json", config)

    store = LocalArtifactStore(output / "artifact-store")
    catalog = LocalArtifactCatalog(
        store.register_run(PROJECT_ID, RUN_ID), run_id=RUN_ID
    )
    config_ref = _put_json(
        catalog,
        kind="v2_2_compiler_benchmark_config",
        schema_version="v2_2_compiler_benchmark_config_v1",
        value=config,
    )

    development = tuple(
        sample
        for sample in dataset.manifest.split("development").samples
        if sample.dataset_role == "regression"
        and sample.source_suite_id == "png_to_shader_v1_m0"
    )
    validation = dataset.manifest.split("validation").samples
    executions: list[_CaseExecution] = []
    for split, samples in (("development", development), ("validation", validation)):
        typed_split = cast(Literal["development", "validation"], split)
        for sample in samples:
            loaded = loaded_intents.get((split, sample.case_id))
            execution = _CaseExecution(
                split=typed_split,
                sample=sample,
                intent=None if loaded is None else loaded[0],
            )
            if loaded is not None:
                execution.refs["input_intent_ref"] = _artifact_projection(loaded[1])
            _execute_case_static(
                execution,
                catalog=catalog,
                intent_bytes=None if loaded is None else loaded[2],
            )
            executions.append(execution)

    if with_webgl:
        asyncio.run(_execute_webgl(executions, catalog=catalog))

    outcomes: list[V2_2CompilerCaseOutcome] = []
    case_record_refs: list[ArtifactRefV2] = []
    for execution in executions:
        outcome = _outcome(
            execution,
            gate=gate,
            config_sha256=config_sha256,
            input_intent_outcomes_sha256=input_outcomes_sha256,
            with_webgl=with_webgl,
        )
        outcome_ref = _put_json(
            catalog,
            kind="v2_2_compiler_case_outcome",
            schema_version="v2_2_compiler_case_outcome_v1",
            value=outcome,
        )
        execution.refs["failure"] = _artifact_projection(execution.failure_ref)
        execution.refs["outcome"] = _artifact_projection(outcome_ref)
        case_record_refs.append(
            _put_json(
                catalog,
                kind="v2_2_compiler_case_record",
                schema_version="v2_2_compiler_case_record_v1",
                value={
                    "schema_version": "v2_2_compiler_case_record_v1",
                    "execution_mode": EXECUTION_MODE,
                    "model_calls": 0,
                    "case_id": execution.sample.case_id,
                    "split": execution.split,
                    "refs": execution.refs,
                },
            )
        )
        outcomes.append(outcome)

    frozen_outcomes = tuple(outcomes)
    report = evaluate_v2_2_compiler_gate(
        dataset,
        gate,
        frozen_outcomes,
        config_sha256=config_sha256,
        input_intent_outcomes_sha256=input_outcomes_sha256,
        webgl_requested=with_webgl,
    )
    outcomes_ref = _put_json(
        catalog,
        kind="v2_2_compiler_outcome_set",
        schema_version="v2_2_compiler_outcome_set_v1",
        value={
            "schema_version": "v2_2_compiler_outcome_set_v1",
            "config_sha256": config_sha256,
            "input_intent_outcomes_sha256": input_outcomes_sha256,
            "outcomes": frozen_outcomes,
        },
    )
    report_ref = _put_json(
        catalog,
        kind="v2_2_compiler_gate_report",
        schema_version="v2_2_compiler_gate_report_v1",
        value=report,
    )
    summary: dict[str, object] = {
        "schema_version": "v2_2_compiler_benchmark_summary_v1",
        "runner_version": RUNNER_VERSION,
        "execution_mode": EXECUTION_MODE,
        "model_calls": 0,
        "model_provider": None,
        "quality_claim": config["quality_claim"],
        "run_id": RUN_ID,
        "config_sha256": config_sha256,
        "input_intent_outcomes_sha256": input_outcomes_sha256,
        "webgl_requested": with_webgl,
        "ready": report.ready,
        "blockers": report.blockers,
        "case_count": len(frozen_outcomes),
        "success_count": sum(item.success for item in frozen_outcomes),
        "failure_count": sum(not item.success for item in frozen_outcomes),
        "config_ref": _artifact_projection(config_ref),
        "outcomes_ref": _artifact_projection(outcomes_ref),
        "report_ref": _artifact_projection(report_ref),
        "case_record_refs": tuple(
            _artifact_projection(ref) for ref in case_record_refs
        ),
    }
    _write_json(
        output / "outcomes.json",
        [item.model_dump(mode="json") for item in frozen_outcomes],
    )
    _write_json(output / "report.json", report.model_dump(mode="json"))
    _write_json(output / "summary.json", summary)
    return V2_2CompilerBenchmarkRun(
        output_dir=output,
        config=config,
        outcomes=frozen_outcomes,
        report=report,
        summary=summary,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="运行 V2.2 fixture/no-model Genome/Compiler conformance。"
    )
    parser.add_argument("--output", required=True, help="必须尚不存在的输出目录。")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--benchmark-root", default=str(DEFAULT_BENCHMARK_ROOT))
    parser.add_argument(
        "--with-webgl",
        action="store_true",
        help="显式对全部 153 个编译结果执行 WebGL1 compile/link/draw。",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 默认在 gate 非 ready 时返回 2。."""
    args = _parser().parse_args(argv)
    result = run_v2_2_compiler_benchmark(
        args.output,
        manifest_path=args.manifest,
        benchmark_root=args.benchmark_root,
        with_webgl=args.with_webgl,
    )
    sys.stdout.write(
        json.dumps(
            {
                "execution_mode": EXECUTION_MODE,
                "model_calls": 0,
                "output": str(result.output_dir),
                "webgl_requested": args.with_webgl,
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
