"""运行不调用模型的 V2.1 Intent conformance benchmark。."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, fields, is_dataclass
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Literal, Sequence, cast

from pydantic import BaseModel

from shaderforge.analysis import TargetMeasurementsV2ArtifactBundle, measure_target_v2
from shaderforge.benchmark import (
    LoadedV2Dataset,
    V2_1IntentCaseOutcome,
    V2_1IntentGateReport,
    V2DatasetSample,
    V2DatasetStageGate,
    evaluate_v2_1_intent_gate,
    evaluate_v2_dataset_stage_gate,
    load_v2_dataset_manifest,
)
from shaderforge.contracts import canonical_sha256
from shaderforge.contracts.png_to_shader_v1 import WEBGL1_STATIC_NO_TEXTURE_V1
from shaderforge.contracts.taxonomy import REQUIRED_LAYER_ORDER, RequiredLayerTaxon
from shaderforge.intent import (
    ContractConstraintValue,
    HoleCountConstraintValue,
    InstanceCountConstraintValue,
    IntentBuildContext,
    LayerHypothesis,
    PrimitiveCandidate,
    RequiredLayerAssessment,
    RequiredLayerConstraintValue,
    StrategyHypothesis,
    TopologyConstraintValue,
    Uncertainty,
    VisualInterpretationV2,
    build_intent_build_context,
    build_intent_variants,
    build_request_constraint_set,
    parse_visual_interpretation_v2,
    validate_intent_build_result,
    validate_intent_ir,
)
from shaderforge.intent.ir import VisualLayerRole
from shaderforge.intent.models import (
    Constraint,
    ConstraintKind,
    ConstraintSource,
    ConstraintValue,
    RequestConstraintSet,
)
from shaderforge.store import (
    ArtifactRefV2,
    LocalArtifactCatalog,
    LocalArtifactStore,
)

RUNNER_VERSION: Literal["v2_1_intent_fixture_benchmark_v1"] = (
    "v2_1_intent_fixture_benchmark_v1"
)
FIXTURE_POLICY_VERSION: Literal["taxonomy_allowlist_fixture_v1"] = (
    "taxonomy_allowlist_fixture_v1"
)
EXECUTION_MODE: Literal["fixture/no-model"] = "fixture/no-model"
RUN_ID = "v2-1-intent-fixture-v1"
PROJECT_ID = "v2-1-intent-benchmark"

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "benchmarks/png_to_shader_v2/dataset_manifest.v1.json"
DEFAULT_BENCHMARK_ROOT = ROOT / "benchmarks"


@dataclass(frozen=True)
class V2_1IntentBenchmarkRun:
    """一次 fixture/no-model benchmark 的本地结果。."""

    output_dir: Path
    config: dict[str, object]
    outcomes: tuple[V2_1IntentCaseOutcome, ...]
    report: V2_1IntentGateReport
    summary: dict[str, object]


@dataclass
class _CaseRefs:
    source: ArtifactRefV2 | None = None
    measurements: ArtifactRefV2 | None = None
    constraint_set: ArtifactRefV2 | None = None
    interpretation: ArtifactRefV2 | None = None
    intent_build_result: ArtifactRefV2 | None = None
    intent: ArtifactRefV2 | None = None
    failure: ArtifactRefV2 | None = None
    outcome: ArtifactRefV2 | None = None


def _json_default(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
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


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _artifact_projection(ref: ArtifactRefV2 | None) -> object:
    return None if ref is None else asdict(ref)


def _case_outcome(
    gate: V2DatasetStageGate,
    sample: V2DatasetSample,
    *,
    split: Literal["development", "validation"],
    intent_valid: bool,
    predicted_topology: Literal["solid", "hollow", "ring", "open"] | None = None,
    predicted_instance_count: int | None = None,
    predicted_required_layers: tuple[RequiredLayerTaxon, ...] = (),
    failure_code: str | None = None,
) -> V2_1IntentCaseOutcome:
    return V2_1IntentCaseOutcome(
        manifest_id=gate.manifest_id,
        dataset_version=gate.dataset_version,
        manifest_sha256=gate.manifest_sha256,
        taxonomy_sha256=gate.taxonomy_sha256,
        split=split,
        case_id=sample.case_id,
        intent_valid=intent_valid,
        predicted_topology=predicted_topology,
        predicted_instance_count=predicted_instance_count,
        predicted_required_layers=predicted_required_layers,
        failure_code=failure_code,
    )


def _constraint(
    *,
    kind: ConstraintKind,
    value: ConstraintValue,
    source: ConstraintSource,
    scope: Literal["global", "object"],
    scope_ref: str | None,
    evidence_ref: ArtifactRefV2,
) -> Constraint:
    return Constraint(
        constraint_id=f"pending-{kind}",
        kind=kind,
        strength="hard",
        scope=scope,
        scope_ref=scope_ref,
        value=value,
        source=source,
        source_revision=0,
        confidence=1.0,
        verification_status="verified",
        evidence_refs=(evidence_ref,),
    )


def _build_constraints(
    sample: V2DatasetSample,
    bundle: TargetMeasurementsV2ArtifactBundle,
    request_ref: ArtifactRefV2,
    config_ref: ArtifactRefV2,
) -> RequestConstraintSet:
    constraints = [
        _constraint(
            kind="contract",
            value=ContractConstraintValue(
                contract_id=WEBGL1_STATIC_NO_TEXTURE_V1.contract_id
            ),
            source="render_contract",
            scope="global",
            scope_ref=None,
            evidence_ref=config_ref,
        ),
        _constraint(
            kind="topology",
            value=TopologyConstraintValue(topology=sample.topology),
            source="user",
            scope="object",
            scope_ref="subject",
            evidence_ref=request_ref,
        ),
        _constraint(
            kind="instance_count",
            value=InstanceCountConstraintValue(exact_count=sample.instance_count),
            source="user",
            scope="object",
            scope_ref="subject",
            evidence_ref=request_ref,
        ),
        _constraint(
            kind="hole_count",
            value=HoleCountConstraintValue(exact_count=sample.hole_count),
            source="user",
            scope="object",
            scope_ref="subject",
            evidence_ref=request_ref,
        ),
    ]
    constraints.extend(
        _constraint(
            kind="required_layer",
            value=RequiredLayerConstraintValue(layer=layer),
            source="user",
            scope="global" if layer == "background" else "object",
            scope_ref=None if layer == "background" else "subject",
            evidence_ref=request_ref,
        )
        for layer in sample.required_layers
    )
    return build_request_constraint_set(
        constraint_set_id=f"benchmark-request-{sample.case_id}",
        target_sha256=bundle.measurements.target_sha256,
        request_revision=0,
        constraints=constraints,
        evidence_refs=(request_ref, bundle.evidence_index_ref),
    )


def _fixture_interpretation(
    dataset: LoadedV2Dataset,
    sample: V2DatasetSample,
    evidence_ref: ArtifactRefV2,
) -> tuple[VisualInterpretationV2, IntentBuildContext]:
    taxonomy_by_id = {item.primitive_id: item for item in dataset.taxonomy.primitives}
    selected = tuple(
        taxonomy_by_id[primitive_id]
        for primitive_id in sample.expected_primitives.items
    )
    primitive_ids = tuple(item.primitive_id for item in selected)
    template_ids = tuple(dict.fromkeys(item.template_id for item in selected))
    layer_role: VisualLayerRole = "base_fill"
    layer_id = "fixture-base-fill"
    interpretation = VisualInterpretationV2(
        summary=(
            "fixture/no-model conformance interpretation；不代表 VLM 视觉理解质量。"
        ),
        layer_hypotheses=(
            LayerHypothesis(
                layer_id=layer_id,
                role=layer_role,
                order=0,
                confidence=0.5,
                region_description="fixture 仅建立可验证的基础层。",
                primitive_candidates=primitive_ids,
                evidence_refs=(evidence_ref,),
            ),
        ),
        required_layer_assessments=tuple(
            RequiredLayerAssessment(
                layer=layer,
                status=(
                    "required" if layer in sample.required_layers else "not_required"
                ),
                confidence=0.5,
                rationale="fixture 标签闭集，仅用于 conformance。",
                evidence_refs=(evidence_ref,),
            )
            for layer in REQUIRED_LAYER_ORDER
        ),
        primitive_candidates=tuple(
            PrimitiveCandidate(
                candidate_id=f"fixture-primitive-{index:02d}",
                primitive_id=primitive_id,
                layer_id=layer_id,
                confidence=0.5,
                evidence_refs=(evidence_ref,),
            )
            for index, primitive_id in enumerate(primitive_ids)
        ),
        strategy_hypotheses=(
            StrategyHypothesis(
                strategy_id="fixture-taxonomy-strategy",
                template_ids=template_ids,
                required_layer_ids=(layer_id,),
                complexity="low",
                confidence=0.5,
                evidence_refs=(evidence_ref,),
            ),
        ),
        uncertainties=(
            Uncertainty(
                uncertainty_id="fixture-not-vlm-quality",
                subject="visual_interpretation",
                description="本结果来自 taxonomy fixture，不是模型视觉判断。",
                severity="high",
                evidence_refs=(evidence_ref,),
            ),
        ),
        evidence_refs=(evidence_ref,),
    )
    context = build_intent_build_context(
        contract_id=WEBGL1_STATIC_NO_TEXTURE_V1.contract_id,
        primitive_catalog_sha256=dataset.taxonomy_sha256,
        template_catalog_sha256=dataset.taxonomy_sha256,
        allowed_primitive_ids=(
            item.primitive_id for item in dataset.taxonomy.primitives
        ),
        allowed_template_ids=(item.template_id for item in dataset.taxonomy.primitives),
        allowed_interpretation_evidence_refs=(evidence_ref,),
    )
    return interpretation, context


def _freeze_source_bytes(
    dataset: LoadedV2Dataset,
    samples: Sequence[V2DatasetSample],
) -> dict[str, bytes]:
    """在写入任何运行产物前冻结并复验全部输入图片。."""
    frozen: dict[str, bytes] = {}
    for sample in samples:
        source_bytes = dataset.resolve_image(sample).read_bytes()
        actual_sha256 = sha256(source_bytes).hexdigest()
        if actual_sha256 != sample.sha256:
            raise ValueError(
                f"{sample.case_id} 图片在 stage gate 后发生变化："
                f"expected={sample.sha256}, actual={actual_sha256}。"
            )
        frozen[sample.case_id] = source_bytes
    return frozen


def _run_case(
    dataset: LoadedV2Dataset,
    gate: V2DatasetStageGate,
    sample: V2DatasetSample,
    *,
    split: Literal["development", "validation"],
    source_bytes: bytes,
    catalog: LocalArtifactCatalog,
    config_ref: ArtifactRefV2,
) -> tuple[V2_1IntentCaseOutcome, ArtifactRefV2]:
    refs = _CaseRefs()
    phase = "source"
    refs.source = catalog.put(
        run_id=RUN_ID,
        kind="target_source",
        schema_version="target_source_v1",
        content_type="image/png",
        data=source_bytes,
    )
    request_ref = _put_json(
        catalog,
        kind="v2_1_benchmark_case_request",
        schema_version="v2_1_benchmark_case_request_v1",
        value={
            "schema_version": "v2_1_benchmark_case_request_v1",
            "case_id": sample.case_id,
            "split": split,
            "source_sha256": sample.sha256,
            "request_constraints": {
                "topology": sample.topology,
                "instance_count": sample.instance_count,
                "hole_count": sample.hole_count,
                "required_layers": sample.required_layers,
            },
            "expected_primitives_taxonomy_version": (
                sample.expected_primitives.taxonomy_version
            ),
            "expected_primitives": sample.expected_primitives.items,
        },
    )
    bundle: TargetMeasurementsV2ArtifactBundle | None = None
    try:
        phase = "measurements"
        bundle = measure_target_v2(source_bytes, catalog=catalog, run_id=RUN_ID)
        if bundle.target_source_ref != refs.source:
            raise ValueError("measure_target_v2 source Artifact identity 漂移。")
        refs.measurements = bundle.measurements_ref

        phase = "constraint_set"
        constraint_set = _build_constraints(
            sample,
            bundle,
            request_ref,
            config_ref,
        )
        refs.constraint_set = _put_json(
            catalog,
            kind="request_constraint_set",
            schema_version="request_constraint_set_v1",
            value=constraint_set,
        )

        phase = "interpretation"
        fixture, context = _fixture_interpretation(
            dataset,
            sample,
            bundle.measurements_ref,
        )
        interpretation = parse_visual_interpretation_v2(fixture.model_dump_json())
        refs.interpretation = _put_json(
            catalog,
            kind="visual_interpretation",
            schema_version="visual_interpretation_v2_1",
            value=interpretation,
        )

        phase = "intent"
        build_result = build_intent_variants(
            bundle.measurements,
            interpretation,
            constraint_set,
            context,
        )
        validate_intent_build_result(
            build_result,
            measurements=bundle.measurements,
            interpretation=interpretation,
            constraint_set=constraint_set,
            context=context,
        )
        refs.intent_build_result = _put_json(
            catalog,
            kind="intent_build_result",
            schema_version="intent_build_result_v3",
            value=build_result,
        )
        if not build_result.variants:
            reasons = sorted(
                {
                    reason
                    for rejection in build_result.rejections
                    for reason in rejection.reason_codes
                }
            )
            raise ValueError(f"没有可行 Intent variant：{','.join(reasons)}")
        intent = min(build_result.variants, key=lambda item: item.intent_id)
        validate_intent_ir(
            intent,
            measurements=bundle.measurements,
            interpretation=interpretation,
            constraint_set=constraint_set,
            context=context,
        )
        refs.intent = _put_json(
            catalog,
            kind="intent_ir",
            schema_version="intent_v3",
            value=intent,
        )
        subject = intent.objects[0]
        predicted_required_layers = tuple(
            layer.role for layer in intent.layers if layer.required
        )
        outcome = _case_outcome(
            gate,
            sample,
            split=split,
            intent_valid=True,
            predicted_topology=subject.topology,
            predicted_instance_count=subject.instance_count,
            predicted_required_layers=predicted_required_layers,
        )
    except (OSError, ValueError) as exc:
        failure_code = f"{phase}_failed"
        refs.failure = _put_json(
            catalog,
            kind="v2_1_intent_case_failure",
            schema_version="v2_1_intent_case_failure_v1",
            value={
                "schema_version": "v2_1_intent_case_failure_v1",
                "execution_mode": EXECUTION_MODE,
                "case_id": sample.case_id,
                "split": split,
                "phase": phase,
                "failure_code": failure_code,
                "error_type": type(exc).__name__,
                "message": str(exc),
                "source_ref": _artifact_projection(refs.source),
                "measurements_ref": _artifact_projection(refs.measurements),
                "constraint_set_ref": _artifact_projection(refs.constraint_set),
                "interpretation_ref": _artifact_projection(refs.interpretation),
            },
        )
        outcome = _case_outcome(
            gate,
            sample,
            split=split,
            intent_valid=False,
            failure_code=failure_code,
        )

    refs.outcome = _put_json(
        catalog,
        kind="v2_1_intent_case_outcome",
        schema_version="v2_1_intent_case_outcome_v1",
        value=outcome,
    )
    case_record_ref = _put_json(
        catalog,
        kind="v2_1_intent_case_record",
        schema_version="v2_1_intent_case_record_v1",
        value={
            "schema_version": "v2_1_intent_case_record_v1",
            "execution_mode": EXECUTION_MODE,
            "model_calls": 0,
            "case_id": sample.case_id,
            "split": split,
            "refs": {
                field.name: _artifact_projection(getattr(refs, field.name))
                for field in fields(refs)
            },
        },
    )
    return outcome, case_record_ref


def run_v2_1_intent_benchmark(
    output_dir: str | Path,
    *,
    manifest_path: str | Path = DEFAULT_MANIFEST,
    benchmark_root: str | Path = DEFAULT_BENCHMARK_ROOT,
) -> V2_1IntentBenchmarkRun:
    """运行真实 10+validation conformance；绝不读取 release-held-out。."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    dataset = load_v2_dataset_manifest(
        manifest_path,
        benchmark_root=benchmark_root,
        gate_stage="v2_1_intent",
    )
    gate = evaluate_v2_dataset_stage_gate(dataset, stage="v2_1_intent")
    if not gate.ready:
        raise ValueError(f"V2.1 dataset stage gate 未通过：{gate.blockers}")
    development = tuple(
        sample
        for sample in dataset.manifest.split("development").samples
        if sample.dataset_role == "regression"
        and sample.source_suite_id == "png_to_shader_v1_m0"
    )
    validation = dataset.manifest.split("validation").samples
    if len(development) != 10 or len(validation) != 41:
        raise ValueError(
            "V2.1 fixture runner 要求冻结 development 10 + validation 41。"
        )
    frozen_sources = _freeze_source_bytes(
        dataset,
        (*development, *validation),
    )

    config_payload: dict[str, object] = {
        "schema_version": "v2_1_intent_benchmark_config_v1",
        "runner_version": RUNNER_VERSION,
        "fixture_policy_version": FIXTURE_POLICY_VERSION,
        "execution_mode": EXECUTION_MODE,
        "model_calls_allowed": False,
        "model_call_budget": 0,
        "quality_claim": "conformance_only_not_vlm_quality",
        "run_id": RUN_ID,
        "gate_stage": "v2_1_intent",
        "manifest_id": gate.manifest_id,
        "dataset_version": gate.dataset_version,
        "manifest_sha256": gate.manifest_sha256,
        "taxonomy_sha256": gate.taxonomy_sha256,
        "development_case_count": len(development),
        "validation_case_count": len(validation),
    }
    config_hash = canonical_sha256(config_payload)
    config = {**config_payload, "config_sha256": config_hash}
    _write_json(output / "config.json", config)

    store = LocalArtifactStore(output / "artifact-store")
    run_store = store.register_run(PROJECT_ID, RUN_ID)
    catalog = LocalArtifactCatalog(run_store, run_id=RUN_ID)
    config_ref = _put_json(
        catalog,
        kind="v2_1_intent_benchmark_config",
        schema_version="v2_1_intent_benchmark_config_v1",
        value=config,
    )
    outcomes: list[V2_1IntentCaseOutcome] = []
    case_record_refs: list[ArtifactRefV2] = []
    for split, samples in (
        ("development", development),
        ("validation", validation),
    ):
        typed_split = cast(Literal["development", "validation"], split)
        for sample in samples:
            outcome, case_record_ref = _run_case(
                dataset,
                gate,
                sample,
                split=typed_split,
                source_bytes=frozen_sources[sample.case_id],
                catalog=catalog,
                config_ref=config_ref,
            )
            outcomes.append(outcome)
            case_record_refs.append(case_record_ref)

    frozen_outcomes = tuple(outcomes)
    report = evaluate_v2_1_intent_gate(dataset, gate, frozen_outcomes)
    outcomes_ref = _put_json(
        catalog,
        kind="v2_1_intent_outcome_set",
        schema_version="v2_1_intent_outcome_set_v1",
        value={
            "schema_version": "v2_1_intent_outcome_set_v1",
            "config_sha256": config_hash,
            "outcomes": frozen_outcomes,
        },
    )
    report_ref = _put_json(
        catalog,
        kind="v2_1_intent_gate_report",
        schema_version="v2_1_intent_gate_report_v1",
        value=report,
    )
    summary: dict[str, object] = {
        "schema_version": "v2_1_intent_benchmark_summary_v1",
        "runner_version": RUNNER_VERSION,
        "execution_mode": EXECUTION_MODE,
        "model_calls": 0,
        "model_provider": None,
        "quality_claim": "conformance_only_not_vlm_quality",
        "run_id": RUN_ID,
        "config_sha256": config_hash,
        "ready": report.ready,
        "blockers": report.blockers,
        "case_count": len(frozen_outcomes),
        "success_count": sum(item.intent_valid for item in frozen_outcomes),
        "failure_count": sum(not item.intent_valid for item in frozen_outcomes),
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
    return V2_1IntentBenchmarkRun(
        output_dir=output,
        config=config,
        outcomes=frozen_outcomes,
        report=report,
        summary=summary,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="运行 V2.1 fixture/no-model Intent conformance benchmark。"
    )
    parser.add_argument("--output", required=True, help="必须尚不存在的输出目录。")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--benchmark-root", default=str(DEFAULT_BENCHMARK_ROOT))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 默认在 gate 非 ready 时返回 2。."""
    args = _parser().parse_args(argv)
    result = run_v2_1_intent_benchmark(
        args.output,
        manifest_path=args.manifest,
        benchmark_root=args.benchmark_root,
    )
    sys.stdout.write(
        json.dumps(
            {
                "execution_mode": EXECUTION_MODE,
                "model_calls": 0,
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
