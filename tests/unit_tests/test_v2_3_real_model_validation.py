from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.run_v2_1_intent_benchmark as intent_runner
import scripts.run_v2_3_real_model_validation as runner
from agent.app.prompts.prompt_loader import PromptDefinition
from agent.app.services.png_to_shader_v2 import (
    DurableGatewayResultV1,
    RealModelCallPolicyV1,
    VisualInterpretationGatewayAdapter,
)
from shaderforge.benchmark import (
    V2_3GraphGateReport,
    evaluate_v2_dataset_stage_gate,
    load_v2_dataset_manifest,
)
from shaderforge.benchmark.v2_3_real_model_validation import (
    V2_3RealBudgetV1,
    V2_3RealCaseOutcome,
    V2_3RealModelIdentityV1,
    V2_3RealModelValidationReport,
    V2_3RealUsageV1,
    compute_v2_3_real_report_sha256,
    evaluate_v2_3_real_model_validation,
)
from shaderforge.store import ArtifactRefV2

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "benchmarks/png_to_shader_v2/dataset_manifest.v1.json"
PROMPT = PromptDefinition(
    name="analyze_visual_layers_v2",
    version="real-validation-test-v1",
    prompt="只返回 VisualInterpretationV2 JSON。",
)


def _budget() -> V2_3RealBudgetV1:
    return V2_3RealBudgetV1(
        wall_time_ms=30_000,
        model_calls=1,
        max_input_tokens=1_000,
        max_output_tokens=2_000,
        render_calls=100,
        candidate_attempts=100,
        artifact_bytes=50_000_000,
        cost_usd_micros=100_000,
    )


def _policy() -> RealModelCallPolicyV1:
    return RealModelCallPolicyV1(
        provider_id="fake-durable",
        model_id="fake:model-v1",
        pricing_policy_id="fake-price-v1",
        input_micros_per_million_tokens=1_000_000,
        output_micros_per_million_tokens=2_000_000,
        max_input_tokens=1_000,
        max_output_tokens=2_000,
        max_cost_usd_micros=100_000,
        max_output_artifact_bytes=1_000_000,
    )


def _identity(policy: RealModelCallPolicyV1) -> V2_3RealModelIdentityV1:
    from hashlib import sha256

    return V2_3RealModelIdentityV1(
        provider_id=policy.provider_id,
        model_id=policy.model_id,
        prompt_name=PROMPT.name,
        prompt_version=PROMPT.version,
        prompt_sha256=sha256(PROMPT.prompt.encode()).hexdigest(),
        pricing_policy_id=policy.pricing_policy_id,
        pricing_policy_sha256=policy.pricing_policy_sha256,
    )


def _zero_usage() -> V2_3RealUsageV1:
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


class _Gateway:
    def __init__(self, dataset, sample, *, mode: str = "success") -> None:
        self.dataset = dataset
        self.sample = sample
        self.mode = mode
        self.calls = 0
        self.result: DurableGatewayResultV1 | None = None

    async def recover(self, invocation_id: str) -> DurableGatewayResultV1 | None:
        if self.result is None:
            return None
        return self.result.model_copy(update={"invocation_id": invocation_id})

    async def invoke_once(self, *, invocation_id: str, messages, options):
        del options
        self.calls += 1
        parts = messages[1].content
        authorized_text = next(
            part["text"]
            for part in parts
            if isinstance(part, dict)
            and isinstance(part.get("text"), str)
            and part["text"].startswith("authorized_evidence_refs")
        )
        payload = authorized_text.split("<authorized_evidence_refs>", 1)[1].split(
            "</authorized_evidence_refs>", 1
        )[0]
        evidence_ref = ArtifactRefV2(**json.loads(payload)[0])
        interpretation, _context = intent_runner._fixture_interpretation(  # noqa: SLF001
            self.dataset, self.sample, evidence_ref
        )
        raw_response = (
            '{"invalid":true}'
            if self.mode == "parse_failure"
            else interpretation.model_dump_json()
        )
        actual_model = "fake:wrong" if self.mode == "identity" else "fake:model-v1"
        self.result = DurableGatewayResultV1(
            invocation_id=invocation_id,
            provider_receipt_id=f"receipt-{self.sample.case_id}",
            provider_id="fake-durable",
            requested_model_id="fake:model-v1",
            actual_model_id=actual_model,
            raw_response=raw_response,
            input_tokens=40,
            output_tokens=30,
        )
        return self.result


def _dataset_and_sample():
    dataset = load_v2_dataset_manifest(
        MANIFEST,
        benchmark_root=ROOT / "benchmarks",
        gate_stage="v2_3_graph_conformance",
    )
    sample = next(
        item
        for item in dataset.manifest.split("development").samples
        if item.dataset_role == "regression"
        and item.source_suite_id == "png_to_shader_v1_m0"
    )
    return dataset, sample


def test_one_real_case_uses_service_graph_and_repeat_resume_is_free(
    tmp_path: Path,
) -> None:
    dataset, sample = _dataset_and_sample()
    policy = _policy()
    gateway = _Gateway(dataset, sample)
    outcome = runner._run_case(  # noqa: SLF001
        case_root=tmp_path / "case",
        dataset=dataset,
        sample=sample,
        split="development",
        source_bytes=dataset.resolve_image(sample).read_bytes(),
        suite_run_id="real-test-suite",
        run_id="real-test-case",
        adapter=VisualInterpretationGatewayAdapter(
            gateway=gateway,
            prompt=PROMPT,
            policy=policy,
        ),
        policy=policy,
        case_budget=_budget(),
        model_identity=_identity(policy),
        config_sha256="a" * 64,
    )

    assert outcome.success
    assert outcome.terminal_phase == "finalized"
    assert outcome.resume_zero_new_charge_verified
    assert outcome.budget_used.model_calls == 1
    assert outcome.budget_used.input_tokens == 40
    assert outcome.budget_used.output_tokens == 30
    assert outcome.intent_variant_count >= 1
    assert outcome.target_structure_branch_count >= 1
    assert gateway.calls == 1


@pytest.mark.parametrize(
    ("mode", "failure_code"),
    (
        ("parse_failure", "model_parse_failed"),
        ("identity", "model_identity_failed"),
    ),
)
def test_real_case_preserves_model_failures_in_denominator(
    tmp_path: Path, mode: str, failure_code: str
) -> None:
    dataset, sample = _dataset_and_sample()
    policy = _policy()
    gateway = _Gateway(dataset, sample, mode=mode)
    outcome = runner._run_case(  # noqa: SLF001
        case_root=tmp_path / mode,
        dataset=dataset,
        sample=sample,
        split="development",
        source_bytes=dataset.resolve_image(sample).read_bytes(),
        suite_run_id=f"real-{mode}",
        run_id=f"real-{mode}-case",
        adapter=VisualInterpretationGatewayAdapter(
            gateway=gateway,
            prompt=PROMPT,
            policy=policy,
        ),
        policy=policy,
        case_budget=_budget(),
        model_identity=_identity(policy),
        config_sha256="b" * 64,
    )

    assert not outcome.success
    assert outcome.failure_code == failure_code
    assert gateway.calls == 1
    if mode == "parse_failure":
        assert outcome.resume_zero_new_charge_verified
        assert outcome.budget_used.model_calls == 1
    else:
        assert outcome.resume_zero_new_charge_verified
        assert outcome.budget_used.model_calls == 1
        assert outcome.budget_used.model_tokens == _budget().model_tokens
        assert outcome.budget_reserved.model_calls == 0


def test_pure_report_keeps_10_plus_41_and_rejects_tamper_and_conformance_parse(
    tmp_path: Path,
) -> None:
    del tmp_path
    dataset = load_v2_dataset_manifest(
        MANIFEST,
        benchmark_root=ROOT / "benchmarks",
        gate_stage="v2_3_graph_conformance",
    )
    gate = evaluate_v2_dataset_stage_gate(dataset, stage="v2_3_graph_conformance")
    policy = _policy()
    budget = _budget()
    identity = _identity(policy)
    indexed_samples = tuple(
        (split, sample)
        for split, samples in (
            (
                "development",
                tuple(
                    item
                    for item in dataset.manifest.split("development").samples
                    if item.dataset_role == "regression"
                    and item.source_suite_id == "png_to_shader_v1_m0"
                ),
            ),
            ("validation", dataset.manifest.split("validation").samples),
        )
        for sample in samples
    )
    outcomes = tuple(
        V2_3RealCaseOutcome(
            suite_run_id="aggregate-suite",
            manifest_id=gate.manifest_id,
            dataset_version=gate.dataset_version,
            manifest_sha256=gate.manifest_sha256,
            taxonomy_sha256=gate.taxonomy_sha256,
            config_sha256="c" * 64,
            split=split,
            case_id=sample.case_id,
            run_id=f"failure-{index}",
            model_identity=identity,
            budget_limit=budget,
            budget_used=_zero_usage(),
            budget_reserved=_zero_usage(),
            success=False,
            failure_code="service_execution_failed",
            error_type="SyntheticFailure",
            terminal_phase=None,
            stop_reason=None,
            resume_zero_new_charge_verified=False,
            visual_interpretation_sha256=None,
            request_constraint_set_sha256=None,
            intent_variant_count=0,
            target_structure_branch_count=0,
            objective_best_sha256=None,
            candidate_summary_count=0,
            provider_receipt_id=None,
        )
        for index, (split, sample) in enumerate(indexed_samples)
    )
    report = evaluate_v2_3_real_model_validation(
        dataset,
        gate,
        outcomes,
        suite_run_id="aggregate-suite",
        config_sha256="c" * 64,
        model_identity=identity,
        case_budget=budget,
        suite_budget=budget.scaled(51),
    )
    assert report.visible_validation_complete
    assert report.case_count == 51
    assert report.failure_count == 51
    assert report.release_ready is False
    assert report.vlm_quality_claim == "not_evaluated"

    payload = report.model_dump(mode="json")
    payload["success_count"] = 1
    with pytest.raises(ValueError):
        V2_3RealModelValidationReport.model_validate(payload, strict=True)
    with pytest.raises(ValueError):
        V2_3GraphGateReport.model_validate(
            report.model_dump(mode="json"), strict=True
        )

    duplicate_run = list(outcomes)
    duplicate_run[-1] = duplicate_run[-1].model_copy(
        update={"run_id": duplicate_run[0].run_id}
    )
    with pytest.raises(ValueError, match="唯一 run_id"):
        evaluate_v2_3_real_model_validation(
            dataset,
            gate,
            tuple(duplicate_run),
            suite_run_id="aggregate-suite",
            config_sha256="c" * 64,
            model_identity=identity,
            case_budget=budget,
            suite_budget=budget.scaled(51),
        )


def test_output_artifact_budget_failure_is_closed_and_resume_is_free(
    tmp_path: Path,
) -> None:
    dataset, sample = _dataset_and_sample()
    policy = _policy().model_copy(update={"max_output_artifact_bytes": 1})
    gateway = _Gateway(dataset, sample)
    outcome = runner._run_case(  # noqa: SLF001
        case_root=tmp_path / "output-budget",
        dataset=dataset,
        sample=sample,
        split="development",
        source_bytes=dataset.resolve_image(sample).read_bytes(),
        suite_run_id="real-output-budget",
        run_id="real-output-budget-case",
        adapter=VisualInterpretationGatewayAdapter(
            gateway=gateway,
            prompt=PROMPT,
            policy=policy,
        ),
        policy=policy,
        case_budget=_budget(),
        model_identity=_identity(policy),
        config_sha256="d" * 64,
    )

    assert not outcome.success
    assert outcome.failure_code == "model_output_budget_exceeded"
    assert outcome.resume_zero_new_charge_verified
    assert outcome.budget_used.model_calls == 1
    assert outcome.budget_reserved.model_calls == 0
    assert gateway.calls == 1


def test_full_visible_real_runner_uses_51_fake_durable_gateways(
    tmp_path: Path,
) -> None:
    dataset = load_v2_dataset_manifest(
        MANIFEST,
        benchmark_root=ROOT / "benchmarks",
        gate_stage="v2_3_graph_conformance",
    )
    samples = {
        sample.case_id: sample
        for split in ("development", "validation")
        for sample in dataset.manifest.split(split).samples
    }
    gateways: dict[str, _Gateway] = {}

    def factory(context: runner.V2_3ProviderFactoryContext) -> _Gateway:
        gateway = _Gateway(dataset, samples[context.case_id])
        gateways[context.run_id] = gateway
        return gateway

    policy = _policy()
    budget = _budget()
    output = tmp_path / "visible-real-suite"
    result = runner.run_v2_3_real_model_validation(
        output,
        suite_run_id="fake-durable-visible-51",
        provider_factory=factory,
        prompt=PROMPT,
        policy=policy,
        case_budget=budget,
        suite_budget=budget.scaled(51),
        execution_mode="real",
        allow_model_calls=True,
        enable_real_model=True,
    )

    assert len(gateways) == 51
    assert all(gateway.calls == 1 for gateway in gateways.values())
    assert len(result.outcomes) == 51
    assert all(outcome.success for outcome in result.outcomes)
    assert all(outcome.resume_zero_new_charge_verified for outcome in result.outcomes)
    assert result.report.development.case_count == 10
    assert result.report.validation.case_count == 41
    assert result.report.usage.model_calls == 51
    assert result.report.usage.input_tokens == 51 * 40
    assert result.report.usage.output_tokens == 51 * 30
    assert result.report.usage.model_tokens == 51 * 70
    assert result.report.usage.cost_usd_micros == 51 * 100
    assert result.report.reserved == _zero_usage()
    assert result.report.release_ready is False
    assert result.report.production_admission_enabled is False
    assert result.report.release_held_out_accessed is False
    assert result.report.report_sha256 == compute_v2_3_real_report_sha256(
        result.report
    )
    assert set(path.name for path in output.iterdir()) == {
        "cases",
        "config.json",
        "outcomes.json",
        "report.json",
        "summary.json",
    }
    outcome_files = tuple((output / "cases").glob("*/*/outcome.json"))
    assert len(outcome_files) == 51
    assert all(path.read_bytes().endswith(b"\n") for path in outcome_files)
    persisted_report = V2_3RealModelValidationReport.model_validate_json(
        (output / "report.json").read_bytes(), strict=True
    )
    assert persisted_report == result.report

    factory_calls_before = len(gateways)
    with pytest.raises(FileExistsError):
        runner.run_v2_3_real_model_validation(
            output,
            suite_run_id="fake-durable-visible-51-repeat",
            provider_factory=factory,
            prompt=PROMPT,
            policy=policy,
            case_budget=budget,
            suite_budget=budget.scaled(51),
            execution_mode="real",
            allow_model_calls=True,
            enable_real_model=True,
        )
    assert len(gateways) == factory_calls_before


def test_runner_fails_before_output_without_factory_or_suite_budget(
    tmp_path: Path,
) -> None:
    policy = _policy()
    budget = _budget()
    with pytest.raises(ValueError, match="provider factory"):
        runner.run_v2_3_real_model_validation(
            tmp_path / "missing-provider",
            suite_run_id="missing-provider",
            provider_factory=None,
            prompt=PROMPT,
            policy=policy,
            case_budget=budget,
            suite_budget=budget.scaled(51),
            execution_mode="real",
            allow_model_calls=True,
            enable_real_model=True,
        )
    assert not (tmp_path / "missing-provider").exists()

    calls = 0

    def factory(_context):
        nonlocal calls
        calls += 1
        raise AssertionError("预算不足时不得构造 provider")

    with pytest.raises(ValueError, match="suite 七维预算"):
        runner.run_v2_3_real_model_validation(
            tmp_path / "insufficient-suite",
            suite_run_id="insufficient-suite",
            provider_factory=factory,
            prompt=PROMPT,
            policy=policy,
            case_budget=budget,
            suite_budget=budget,
            execution_mode="real",
            allow_model_calls=True,
            enable_real_model=True,
        )
    assert calls == 0
    assert not (tmp_path / "insufficient-suite").exists()
