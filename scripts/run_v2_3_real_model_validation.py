"""运行独立 V2.3 real-model visible development/validation suite。"""
# ruff: noqa: D415

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, Protocol, cast
from uuid import uuid4

from pydantic import BaseModel

from agent.app.prompts.prompt_loader import PromptDefinition, load_prompt_definition
from agent.app.services.png_to_shader_v2 import (
    DurableLLMGateway,
    FixtureIntentInputFactory,
    FixtureIntentInputsV1,
    LocalRealModelOperationStore,
    PngToShaderV2RequestMetadata,
    PngToShaderV2ServiceConfig,
    RealModelCallPolicyV1,
    RealModelCommittedFailure,
    RealModelIdentityError,
    RealModelOperationIncomplete,
    V2DevelopmentServiceError,
    V2WallTimeBudgetExceeded,
    VisualInterpretationGatewayAdapter,
    create_png_to_shader_v2_development_service,
)
from agent.app.states.png_to_shader_v2_state import BudgetVectorV2, PngToShaderV2State
from agent.app.states.png_to_shader_v2_state_store import (
    LocalPngToShaderV2StateStore,
    V2StateCheckpointNotFoundError,
)
from shaderforge.analysis import TargetMeasurementsV2ArtifactBundle
from shaderforge.benchmark import (
    LoadedV2Dataset,
    V2DatasetSample,
    evaluate_v2_dataset_stage_gate,
    load_v2_dataset_manifest,
)
from shaderforge.benchmark.v2_3_real_model_validation import (
    V2_3RealBudgetV1,
    V2_3RealCaseOutcome,
    V2_3RealFailureCode,
    V2_3RealModelIdentityV1,
    V2_3RealModelValidationReport,
    V2_3RealUsageV1,
    evaluate_v2_3_real_model_validation,
)
from shaderforge.contracts import canonical_sha256
from shaderforge.rendering import CompileResult, RenderResult
from shaderforge.store import ArtifactCatalog, ArtifactRefV2
from shaderforge.validation import validate_shader

try:
    intent_runner = importlib.import_module("scripts.run_v2_1_intent_benchmark")
except ModuleNotFoundError:  # pragma: no cover - direct scripts/ invocation
    intent_runner = importlib.import_module("run_v2_1_intent_benchmark")

RUNNER_VERSION: Literal["v2_3_real_model_validation_runner_v1"] = (
    "v2_3_real_model_validation_runner_v1"
)
EXECUTION_MODE: Literal["real"] = "real"
PROJECT_ID = "v2-3-real-model-validation"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "benchmarks/png_to_shader_v2/dataset_manifest.v1.json"
DEFAULT_BENCHMARK_ROOT = ROOT / "benchmarks"
_SUITE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ProviderFactory(Protocol):
    """按 case 创建具备 durable recover/invoke_once 的 provider gateway。"""

    def __call__(self, context: V2_3ProviderFactoryContext) -> DurableLLMGateway:
        """创建一个不在构造阶段调用模型的 durable gateway。"""
        ...


@dataclass(frozen=True)
class V2_3ProviderFactoryContext:
    """provider factory 可见的非图片、非 release case 身份。"""

    suite_run_id: str
    split: Literal["development", "validation"]
    case_id: str
    run_id: str
    provider_id: str
    model_id: str


@dataclass(frozen=True)
class V2_3RealModelValidationRun:
    """一次独立 real-model validation 的本地产物。"""

    output_dir: Path
    config: dict[str, object]
    outcomes: tuple[V2_3RealCaseOutcome, ...]
    report: V2_3RealModelValidationReport
    summary: dict[str, object]


class _ReferenceRenderer:
    """复用 production Graph 的确定性 reference PNG Renderer 边界。"""

    def __init__(self, reference_png: bytes) -> None:
        self._reference_png = reference_png

    async def render(
        self, fragment_source: str, width: int, height: int
    ) -> RenderResult:
        validation = validate_shader(fragment_source)
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
            image_bytes=self._reference_png if validation.valid else None,
            width=width,
            height=height,
            compile=compile_result,
            console_errors=(),
            metadata=None,
            duration_ms=0.0,
        )

    async def close(self) -> None:
        """关闭无外部资源的 fixture Renderer。"""


def _stable_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda item: (
            item.model_dump(mode="json")
            if isinstance(item, BaseModel)
            else str(item)
        ),
    ).encode("utf-8")


def _write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        default=lambda item: (
            item.model_dump(mode="json")
            if isinstance(item, BaseModel)
            else str(item)
        ),
    ).encode("utf-8") + b"\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


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


def _fixture_factory(
    dataset: LoadedV2Dataset,
    sample: V2DatasetSample,
    run_id: str,
) -> Callable[[TargetMeasurementsV2ArtifactBundle, ArtifactCatalog], FixtureIntentInputsV1]:
    def factory(
        bundle: TargetMeasurementsV2ArtifactBundle,
        catalog: ArtifactCatalog,
    ) -> FixtureIntentInputsV1:
        request_ref = _put_json(
            catalog,
            run_id=run_id,
            kind="v2_3_real_model_case_request",
            schema_version="v2_3_real_model_case_request_v1",
            value={
                "schema_version": "v2_3_real_model_case_request_v1",
                "case_id": sample.case_id,
                "topology": sample.topology,
                "instance_count": sample.instance_count,
                "hole_count": sample.hole_count,
                "required_layers": sample.required_layers,
            },
        )
        policy_ref = _put_json(
            catalog,
            run_id=run_id,
            kind="v2_3_real_model_fixture_policy",
            schema_version="v2_3_real_model_fixture_policy_v1",
            value={
                "schema_version": "v2_3_real_model_fixture_policy_v1",
                "quality_claim": "not_evaluated",
                "production_admission_enabled": False,
            },
        )
        constraints = intent_runner._build_constraints(  # noqa: SLF001
            sample, bundle, request_ref, policy_ref
        )
        interpretation, context = intent_runner._fixture_interpretation(  # noqa: SLF001
            dataset, sample, bundle.measurements_ref
        )
        return FixtureIntentInputsV1(
            request_constraint_set=constraints,
            visual_interpretation=interpretation,
            intent_context=context,
        )

    return factory


def _budget_vector(budget: V2_3RealBudgetV1) -> BudgetVectorV2:
    return BudgetVectorV2(
        wall_time_ms=budget.wall_time_ms,
        model_calls=budget.model_calls,
        model_tokens=budget.model_tokens,
        render_calls=budget.render_calls,
        candidate_attempts=budget.candidate_attempts,
        artifact_bytes=budget.artifact_bytes,
        cost_usd_micros=budget.cost_usd_micros,
    )


def _usage_from_state(
    state: PngToShaderV2State | None,
    *,
    reserved: bool,
    input_tokens: int | None,
    output_tokens: int | None,
) -> V2_3RealUsageV1:
    vector = None if state is None else (
        state.budget_state.reserved if reserved else state.budget_state.used
    )
    if vector is None:
        return V2_3RealUsageV1(
            wall_time_ms=0,
            model_calls=0,
            input_tokens=0,
            output_tokens=0,
            model_tokens=0,
            render_calls=0,
            candidate_attempts=0,
            artifact_bytes=0,
            cost_usd_micros=0,
        )
    split_known = input_tokens is not None and output_tokens is not None
    if not split_known and vector.model_tokens == 0:
        input_tokens = 0
        output_tokens = 0
    return V2_3RealUsageV1(
        wall_time_ms=vector.wall_time_ms,
        model_calls=vector.model_calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model_tokens=vector.model_tokens,
        render_calls=vector.render_calls,
        candidate_attempts=vector.candidate_attempts,
        artifact_bytes=vector.artifact_bytes,
        cost_usd_micros=vector.cost_usd_micros,
    )


def _failure_code(exc: BaseException) -> V2_3RealFailureCode:
    if isinstance(exc, RealModelCommittedFailure):
        if exc.status == "parse_failed":
            return "model_parse_failed"
        if exc.status == "output_budget_exceeded":
            return "model_output_budget_exceeded"
        if exc.status == "interpretation_validation_failed":
            return "model_interpretation_validation_failed"
        if exc.status == "receipt_invalid":
            return "model_identity_failed"
        if exc.status == "provider_indeterminate":
            return "model_provider_indeterminate"
    if isinstance(exc, RealModelIdentityError):
        return "model_identity_failed"
    if isinstance(exc, RealModelOperationIncomplete):
        return "model_operation_incomplete"
    if isinstance(exc, V2WallTimeBudgetExceeded):
        return "service_budget_exceeded"
    if isinstance(exc, V2DevelopmentServiceError):
        return "service_execution_failed"
    return "service_execution_failed"


def _load_state_optional(root: Path, run_id: str) -> PngToShaderV2State | None:
    try:
        return LocalPngToShaderV2StateStore(root).load_last_confirmed(run_id)
    except V2StateCheckpointNotFoundError:
        return None


async def _run_case_async(
    *,
    case_root: Path,
    dataset: LoadedV2Dataset,
    sample: V2DatasetSample,
    split: Literal["development", "validation"],
    source_bytes: bytes,
    suite_run_id: str,
    run_id: str,
    adapter: VisualInterpretationGatewayAdapter,
    policy: RealModelCallPolicyV1,
    case_budget: V2_3RealBudgetV1,
    model_identity: V2_3RealModelIdentityV1,
    config_sha256: str,
) -> V2_3RealCaseOutcome:
    artifact_root = case_root / "artifact-store"
    state_root = case_root / "state-store"
    service = create_png_to_shader_v2_development_service(
        artifact_root=artifact_root,
        state_root=state_root,
        fixture_input_factory=cast(
            FixtureIntentInputFactory, _fixture_factory(dataset, sample, run_id)
        ),
        renderer_factory=lambda _state, reference: _ReferenceRenderer(reference),
        real_model_adapter=adapter,
    )
    config = PngToShaderV2ServiceConfig(
        execution_mode="real",
        allow_model_calls=True,
        real_provider_enabled=True,
        production_admission_enabled=False,
        budget_limits=_budget_vector(case_budget),
        real_model_call=policy,
    )
    result = None
    failure: BaseException | None = None
    resume_verified = False
    try:
        result = await service.invoke(
            project_id=PROJECT_ID,
            run_id=run_id,
            source_bytes=source_bytes,
            request_metadata=PngToShaderV2RequestMetadata(
                request_id=f"{suite_run_id}-{split}-{sample.case_id}",
                expected_source_sha256=sample.sha256,
                source_label=f"{split}/{sample.case_id}",
                source_license=next(
                    record.license_id
                    for record in dataset.manifest.source_records
                    if record.source_suite_id == sample.source_suite_id
                ),
            ),
            config=config,
        )
    except BaseException as exc:  # noqa: BLE001 - outcome 必须保留失败分母
        failure = exc

    operation_store = LocalRealModelOperationStore(
        state_root / ".real-model-operation-v2"
    )
    operation_before = operation_store.load_optional(run_id)
    state_before = _load_state_optional(state_root, run_id)
    if operation_before is not None and operation_before.phase == "committed":
        try:
            repeated = await service.resume(run_id=run_id)
        except RealModelCommittedFailure as repeated_failure:
            repeated_state = _load_state_optional(state_root, run_id)
            resume_verified = (
                isinstance(failure, RealModelCommittedFailure)
                and repeated_failure.status == failure.status
                and repeated_state is not None
                and state_before is not None
                and repeated_state.budget_state.used.model_calls
                == state_before.budget_state.used.model_calls
                and repeated_state.budget_state.used.model_tokens
                == state_before.budget_state.used.model_tokens
                and repeated_state.budget_state.used.cost_usd_micros
                == state_before.budget_state.used.cost_usd_micros
                and operation_store.load_optional(run_id) == operation_before
            )
        except BaseException:  # noqa: BLE001 - 只转为失败 outcome
            resume_verified = False
        else:
            resume_verified = (
                failure is None
                and result is not None
                and repeated.final_state == result.final_state
                and repeated.run_manifest_ref == result.run_manifest_ref
                and operation_store.load_optional(run_id) == operation_before
            )

    state = _load_state_optional(state_root, run_id)
    operation = operation_store.load_optional(run_id)
    receipt = None if operation is None else operation.receipt
    closure = None if operation is None else operation.failure_closure
    input_tokens = (
        receipt.input_tokens
        if receipt is not None
        else (None if closure is None else closure.input_tokens)
    )
    output_tokens = (
        receipt.output_tokens
        if receipt is not None
        else (None if closure is None else closure.output_tokens)
    )
    used = _usage_from_state(
        state,
        reserved=False,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    reserved = _usage_from_state(
        state,
        reserved=True,
        input_tokens=None,
        output_tokens=None,
    )
    branches = () if state is None else state.hypothesis_branches
    intent_ids = tuple(dict.fromkeys(branch.intent_ref.artifact_id for branch in branches))
    graph_terminal_valid = (
        state is not None
        and state.phase == "finalized"
        and state.stop_reason
        in {"completed_with_objective_best", "no_valid_candidate"}
        and bool(branches)
    )
    success = failure is None and resume_verified and graph_terminal_valid
    failure_code: V2_3RealFailureCode | None
    if failure is None and not graph_terminal_valid:
        failure_code = "service_execution_failed"
        error_type = "GraphTerminalValidationError"
        success = False
    elif failure is None and not resume_verified:
        failure_code = "resume_verification_failed"
        error_type = "ResumeVerificationError"
        success = False
    elif failure is not None:
        failure_code = _failure_code(failure)
        error_type = type(failure).__name__
    else:
        failure_code = None
        error_type = None
    outcome = V2_3RealCaseOutcome(
        suite_run_id=suite_run_id,
        manifest_id=dataset.manifest.manifest_id,
        dataset_version=dataset.manifest.dataset_version,
        manifest_sha256=dataset.manifest_sha256,
        taxonomy_sha256=dataset.taxonomy_sha256,
        config_sha256=config_sha256,
        split=split,
        case_id=sample.case_id,
        run_id=run_id,
        model_identity=model_identity,
        budget_limit=case_budget,
        budget_used=used,
        budget_reserved=reserved,
        success=success,
        failure_code=failure_code,
        error_type=error_type,
        terminal_phase=None if state is None else state.phase,
        stop_reason=None if state is None else state.stop_reason,
        resume_zero_new_charge_verified=resume_verified,
        visual_interpretation_sha256=(
            None
            if state is None or state.visual_interpretation_ref is None
            else state.visual_interpretation_ref.sha256
        ),
        request_constraint_set_sha256=(
            None if state is None else state.request_constraint_set_ref.sha256
        ),
        intent_variant_count=len(intent_ids),
        target_structure_branch_count=len(branches),
        objective_best_sha256=(
            None
            if state is None or state.objective_best_ref is None
            else state.objective_best_ref.sha256
        ),
        candidate_summary_count=(0 if state is None else len(state.candidate_summary_refs)),
        provider_receipt_id=(
            str(receipt.provider_receipt_id)
            if receipt is not None
            else (
                None
                if closure is None or closure.provider_receipt_id is None
                else str(closure.provider_receipt_id)
            )
        ),
    )
    _write_json_atomic(case_root / "outcome.json", outcome.model_dump(mode="json"))
    return outcome


def _run_case(
    *,
    case_root: Path,
    dataset: LoadedV2Dataset,
    sample: V2DatasetSample,
    split: Literal["development", "validation"],
    source_bytes: bytes,
    suite_run_id: str,
    run_id: str,
    adapter: VisualInterpretationGatewayAdapter,
    policy: RealModelCallPolicyV1,
    case_budget: V2_3RealBudgetV1,
    model_identity: V2_3RealModelIdentityV1,
    config_sha256: str,
) -> V2_3RealCaseOutcome:
    return asyncio.run(
        _run_case_async(
            case_root=case_root,
            dataset=dataset,
            sample=sample,
            split=split,
            source_bytes=source_bytes,
            suite_run_id=suite_run_id,
            run_id=run_id,
            adapter=adapter,
            policy=policy,
            case_budget=case_budget,
            model_identity=model_identity,
            config_sha256=config_sha256,
        )
    )


def _freeze_sources(
    dataset: LoadedV2Dataset,
    samples: tuple[V2DatasetSample, ...],
) -> dict[str, bytes]:
    frozen: dict[str, bytes] = {}
    for sample in samples:
        payload = dataset.resolve_image(sample).read_bytes()
        if sha256(payload).hexdigest() != sample.sha256:
            raise ValueError(f"{sample.case_id} source SHA-256 在运行前漂移。")
        frozen[sample.case_id] = payload
    return frozen


def _validate_authorization(
    *,
    execution_mode: str,
    allow_model_calls: bool,
    enable_real_model: bool,
    provider_factory: ProviderFactory | None,
) -> ProviderFactory:
    if execution_mode != "real":
        raise ValueError("real validation runner 的 execution_mode 必须显式为 real。")
    if not allow_model_calls or not enable_real_model:
        raise ValueError("real validation 必须同时打开 allow-model-calls 与 real-model。")
    if provider_factory is None or not callable(provider_factory):
        raise ValueError("real validation 缺少显式 durable provider factory。")
    return provider_factory


def run_v2_3_real_model_validation(
    output_dir: str | Path,
    *,
    suite_run_id: str,
    provider_factory: ProviderFactory | None,
    prompt: PromptDefinition,
    policy: RealModelCallPolicyV1,
    case_budget: V2_3RealBudgetV1,
    suite_budget: V2_3RealBudgetV1,
    manifest_path: str | Path = DEFAULT_MANIFEST,
    benchmark_root: str | Path = DEFAULT_BENCHMARK_ROOT,
    execution_mode: str,
    allow_model_calls: bool,
    enable_real_model: bool,
) -> V2_3RealModelValidationRun:
    """运行 visible 10+41；无 factory/双开关/完整预算时在首调前失败。"""
    factory = _validate_authorization(
        execution_mode=execution_mode,
        allow_model_calls=allow_model_calls,
        enable_real_model=enable_real_model,
        provider_factory=provider_factory,
    )
    output = Path(output_dir)
    if output.exists():
        raise FileExistsError(f"real validation 输出目录已存在：{output}")
    if not _SUITE_ID.fullmatch(suite_run_id):
        raise ValueError("suite_run_id 必须唯一、非空且只含安全字符。")
    if case_budget.model_calls != 1:
        raise ValueError("每个 visible case 的模型调用硬预算必须恰好为 1。")
    if (
        policy.max_input_tokens != case_budget.max_input_tokens
        or policy.max_output_tokens != case_budget.max_output_tokens
        or policy.max_cost_usd_micros != case_budget.cost_usd_micros
        or policy.max_output_artifact_bytes > case_budget.artifact_bytes
    ):
        raise ValueError("provider policy 与 case token/cost/artifact 硬预算不一致。")
    required_suite = case_budget.scaled(51)
    if not suite_budget.covers(required_suite):
        raise ValueError("suite 七维预算未覆盖 visible 51 case 最坏情况。")

    dataset = load_v2_dataset_manifest(
        manifest_path,
        benchmark_root=benchmark_root,
        gate_stage="v2_3_graph_conformance",
    )
    gate = evaluate_v2_dataset_stage_gate(dataset, stage="v2_3_graph_conformance")
    if not gate.ready:
        raise ValueError(f"visible V2.3 StageGate 未通过：{gate.blockers}")
    development = tuple(
        sample
        for sample in dataset.manifest.split("development").samples
        if sample.dataset_role == "regression"
        and sample.source_suite_id == "png_to_shader_v1_m0"
    )
    validation = dataset.manifest.split("validation").samples
    if len(development) != 10 or len(validation) != 41:
        raise ValueError("real validation 固定要求 development 10 + validation 41。")
    samples = (*development, *validation)
    frozen_sources = _freeze_sources(dataset, samples)
    prompt_sha256 = sha256(prompt.prompt.encode("utf-8")).hexdigest()
    model_identity = V2_3RealModelIdentityV1(
        provider_id=policy.provider_id,
        model_id=policy.model_id,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
        prompt_sha256=prompt_sha256,
        pricing_policy_id=policy.pricing_policy_id,
        pricing_policy_sha256=policy.pricing_policy_sha256,
    )
    config_payload: dict[str, object] = {
        "schema_version": "v2_3_real_model_validation_config_v1",
        "runner_version": RUNNER_VERSION,
        "suite_run_id": suite_run_id,
        "gate_stage": "v2_3_real_model_validation",
        "dataset_gate_stage": gate.stage,
        "execution_mode": "real",
        "allow_model_calls": True,
        "enable_real_model": True,
        "production_admission_enabled": False,
        "release_held_out_accessed": False,
        "renderer_mode": "deterministic_reference_png_fixture_not_chromium",
        "quality_claim": "not_evaluated",
        "manifest_id": gate.manifest_id,
        "dataset_version": gate.dataset_version,
        "manifest_sha256": gate.manifest_sha256,
        "taxonomy_sha256": gate.taxonomy_sha256,
        "model_identity": model_identity,
        "case_budget": case_budget,
        "suite_budget": suite_budget,
        "development_case_count": len(development),
        "validation_case_count": len(validation),
    }
    config_sha256 = canonical_sha256(config_payload)
    config = {**config_payload, "config_sha256": config_sha256}

    prepared: list[
        tuple[
            Literal["development", "validation"],
            V2DatasetSample,
            str,
            VisualInterpretationGatewayAdapter,
        ]
    ] = []
    for split_name, split_samples in (
        ("development", development),
        ("validation", validation),
    ):
        split = cast(Literal["development", "validation"], split_name)
        for sample in split_samples:
            run_hash = sha256(
                f"{suite_run_id}\0{split}\0{sample.case_id}".encode()
            ).hexdigest()[:24]
            run_id = f"v2-real-{run_hash}"
            context = V2_3ProviderFactoryContext(
                suite_run_id=suite_run_id,
                split=split,
                case_id=sample.case_id,
                run_id=run_id,
                provider_id=policy.provider_id,
                model_id=policy.model_id,
            )
            try:
                gateway = factory(context)
                adapter = VisualInterpretationGatewayAdapter(
                    gateway=gateway,
                    prompt=prompt,
                    policy=policy,
                )
            except Exception as exc:
                raise ValueError(
                    f"provider factory preflight 失败：{type(exc).__name__}。"
                ) from exc
            prepared.append((split, sample, run_id, adapter))

    output.mkdir(parents=True, exist_ok=False)
    _write_json_atomic(output / "config.json", config)
    outcomes: list[V2_3RealCaseOutcome] = []
    for split, sample, run_id, adapter in prepared:
        outcomes.append(
            _run_case(
                case_root=output / "cases" / split / sample.case_id,
                dataset=dataset,
                sample=sample,
                split=split,
                source_bytes=frozen_sources[sample.case_id],
                suite_run_id=suite_run_id,
                run_id=run_id,
                adapter=adapter,
                policy=policy,
                case_budget=case_budget,
                model_identity=model_identity,
                config_sha256=config_sha256,
            )
        )
    frozen_outcomes = tuple(outcomes)
    report = evaluate_v2_3_real_model_validation(
        dataset,
        gate,
        frozen_outcomes,
        suite_run_id=suite_run_id,
        config_sha256=config_sha256,
        model_identity=model_identity,
        case_budget=case_budget,
        suite_budget=suite_budget,
    )
    summary: dict[str, object] = {
        "schema_version": "v2_3_real_model_validation_summary_v1",
        "runner_version": RUNNER_VERSION,
        "suite_run_id": suite_run_id,
        "execution_mode": "real",
        "config_sha256": config_sha256,
        "report_sha256": report.report_sha256,
        "outcomes_sha256": report.outcomes_sha256,
        "case_count": report.case_count,
        "success_count": report.success_count,
        "failure_count": report.failure_count,
        "model_calls": report.usage.model_calls,
        "model_tokens": report.usage.model_tokens,
        "cost_usd_micros": report.usage.cost_usd_micros,
        "reserved_model_calls": report.reserved.model_calls,
        "reserved_model_tokens": report.reserved.model_tokens,
        "reserved_cost_usd_micros": report.reserved.cost_usd_micros,
        "visible_validation_complete": report.visible_validation_complete,
        "release_ready": False,
        "vlm_quality_claim": "not_evaluated",
    }
    _write_json_atomic(
        output / "outcomes.json",
        [item.model_dump(mode="json") for item in frozen_outcomes],
    )
    _write_json_atomic(output / "report.json", report.model_dump(mode="json"))
    _write_json_atomic(output / "summary.json", summary)
    return V2_3RealModelValidationRun(
        output_dir=output,
        config=config,
        outcomes=frozen_outcomes,
        report=report,
        summary=summary,
    )


def load_provider_factory(specification: str) -> ProviderFactory:
    """从显式 ``module:callable`` 载入 provider factory。"""
    module_name, separator, attribute = specification.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("provider factory 必须使用 module:callable。")
    factory = getattr(importlib.import_module(module_name), attribute)
    if not callable(factory):
        raise TypeError("provider factory 目标不可调用。")
    return cast(ProviderFactory, factory)


def _budget_from_args(args: argparse.Namespace, prefix: str) -> V2_3RealBudgetV1:
    return V2_3RealBudgetV1(
        wall_time_ms=getattr(args, f"{prefix}_wall_time_ms"),
        model_calls=getattr(args, f"{prefix}_model_calls"),
        max_input_tokens=getattr(args, f"{prefix}_max_input_tokens"),
        max_output_tokens=getattr(args, f"{prefix}_max_output_tokens"),
        render_calls=getattr(args, f"{prefix}_render_calls"),
        candidate_attempts=getattr(args, f"{prefix}_candidate_attempts"),
        artifact_bytes=getattr(args, f"{prefix}_artifact_bytes"),
        cost_usd_micros=getattr(args, f"{prefix}_cost_usd_micros"),
    )


def _add_budget_args(parser: argparse.ArgumentParser, prefix: str) -> None:
    label = prefix.replace("_", "-")
    for name in (
        "wall-time-ms",
        "model-calls",
        "max-input-tokens",
        "max-output-tokens",
        "render-calls",
        "candidate-attempts",
        "artifact-bytes",
        "cost-usd-micros",
    ):
        parser.add_argument(f"--{label}-{name}", type=int, required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="运行 V2.3 独立 real-model visible validation（会产生付费调用）。"
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--suite-run-id", required=True)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--benchmark-root", default=str(DEFAULT_BENCHMARK_ROOT))
    parser.add_argument("--execution-mode", required=True, choices=("real",))
    parser.add_argument("--allow-model-calls", action="store_true")
    parser.add_argument("--enable-real-model", action="store_true")
    parser.add_argument("--provider-factory", required=True)
    parser.add_argument("--provider-id", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--prompt-name", required=True)
    parser.add_argument("--prompt-version", required=True)
    parser.add_argument("--prompt-sha256", required=True)
    parser.add_argument("--pricing-policy-id", required=True)
    parser.add_argument("--input-micros-per-million-tokens", type=int, required=True)
    parser.add_argument("--output-micros-per-million-tokens", type=int, required=True)
    _add_budget_args(parser, "case")
    _add_budget_args(parser, "suite")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """解析全显式授权与预算，并运行独立 real suite。"""
    args = _parser().parse_args(argv)
    case_budget = _budget_from_args(args, "case")
    suite_budget = _budget_from_args(args, "suite")
    prompt = load_prompt_definition(args.prompt_name)
    actual_prompt_sha = sha256(prompt.prompt.encode("utf-8")).hexdigest()
    if prompt.version != args.prompt_version or actual_prompt_sha != args.prompt_sha256:
        raise ValueError("Prompt version/SHA-256 与显式 CLI identity 不一致。")
    policy = RealModelCallPolicyV1(
        provider_id=args.provider_id,
        model_id=args.model_id,
        pricing_policy_id=args.pricing_policy_id,
        input_micros_per_million_tokens=args.input_micros_per_million_tokens,
        output_micros_per_million_tokens=args.output_micros_per_million_tokens,
        max_input_tokens=case_budget.max_input_tokens,
        max_output_tokens=case_budget.max_output_tokens,
        max_cost_usd_micros=case_budget.cost_usd_micros,
        max_output_artifact_bytes=case_budget.artifact_bytes,
    )
    run = run_v2_3_real_model_validation(
        args.output,
        suite_run_id=args.suite_run_id,
        provider_factory=load_provider_factory(args.provider_factory),
        prompt=prompt,
        policy=policy,
        case_budget=case_budget,
        suite_budget=suite_budget,
        manifest_path=args.manifest,
        benchmark_root=args.benchmark_root,
        execution_mode=args.execution_mode,
        allow_model_calls=args.allow_model_calls,
        enable_real_model=args.enable_real_model,
    )
    sys.stdout.write(
        json.dumps(
            {
                "suite_run_id": run.report.suite_run_id,
                "case_count": run.report.case_count,
                "success_count": run.report.success_count,
                "failure_count": run.report.failure_count,
                "report_sha256": run.report.report_sha256,
                "release_ready": False,
                "vlm_quality_claim": "not_evaluated",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n"
    )
    return 0 if run.report.success_count == run.report.case_count else 2


if __name__ == "__main__":
    raise SystemExit(main())
