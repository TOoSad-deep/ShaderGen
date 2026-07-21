from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import scripts.run_v2_3_rendered_structure_benchmark as runner
from agent.app.states.png_to_shader_v2_state import BudgetVectorV2
from shaderforge.analysis import measure_target_v2
from shaderforge.benchmark import (
    V2_3ActualChromiumReplayRunner,
    evaluate_v2_dataset_stage_gate,
    load_v2_dataset_manifest,
)
from shaderforge.rendering import RenderResult
from shaderforge.store import LocalArtifactCatalog, LocalArtifactStore

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "benchmarks/png_to_shader_v2/dataset_manifest.v1.json"


def _budget(**changes: int) -> BudgetVectorV2:
    values = {
        "wall_time_ms": 300_000,
        "model_calls": 0,
        "model_tokens": 0,
        "render_calls": 512,
        "candidate_attempts": 64,
        "artifact_bytes": 536_870_912,
        "cost_usd_micros": 0,
    }
    values.update(changes)
    return BudgetVectorV2(**values)


def test_runner_fails_before_dataset_access_when_output_exists(tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()

    with pytest.raises(FileExistsError):
        runner.run_v2_3_rendered_structure_benchmark(
            output,
            suite_run_id="exclusive-suite",
            case_budget=_budget(),
            manifest_path=tmp_path / "must-not-be-read.json",
        )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    (
        ({"suite_run_id": "bad id"}, "suite_run_id"),
        ({"execution_mode": "real"}, "fixture/no-model"),
        ({"allow_model_calls": True}, "模型开关"),
    ),
)
def test_runner_authorization_fails_closed_before_dataset_access(
    tmp_path: Path,
    kwargs: dict[str, Any],
    message: str,
) -> None:
    arguments: dict[str, Any] = {
        "suite_run_id": "strict-suite",
        "case_budget": _budget(),
        "manifest_path": tmp_path / "must-not-be-read.json",
    }
    arguments.update(kwargs)

    with pytest.raises(ValueError, match=message):
        runner.run_v2_3_rendered_structure_benchmark(tmp_path / "output", **arguments)


@pytest.mark.parametrize(
    "changes",
    (
        {"model_calls": 1},
        {"model_tokens": 1},
        {"cost_usd_micros": 1},
        {"wall_time_ms": 0},
        {"render_calls": 0},
        {"candidate_attempts": 0},
        {"artifact_bytes": 0},
    ),
)
def test_case_budget_is_finite_and_model_dimensions_are_zero(
    tmp_path: Path, changes: dict[str, int]
) -> None:
    with pytest.raises(ValueError, match="预算"):
        runner.run_v2_3_rendered_structure_benchmark(
            tmp_path / "output",
            suite_run_id="budget-suite",
            case_budget=_budget(**changes),
            manifest_path=tmp_path / "must-not-be-read.json",
        )


def test_shared_renderer_leases_do_not_close_suite_renderer() -> None:
    class FakeRenderer:
        def __init__(self) -> None:
            self.render_calls = 0
            self.close_calls = 0

        async def render(
            self, fragment_source: str, width: int, height: int
        ) -> RenderResult:
            del fragment_source, width, height
            self.render_calls += 1
            return cast(RenderResult, object())

        async def close(self) -> None:
            self.close_calls += 1

    owner = runner._SharedSuiteRenderer()  # noqa: SLF001
    fake = FakeRenderer()
    owner.renderer = cast(Any, fake)
    owner.started = True
    lease_a = owner.lease(cast(Any, object()), b"png")
    lease_b = owner.lease(cast(Any, object()), b"png")

    async def exercise() -> None:
        await lease_a.render("a", 8, 8)
        await lease_a.close()
        await lease_a.close()
        await lease_b.render("b", 8, 8)
        await lease_b.close()
        assert fake.close_calls == 0
        await owner.close()

    asyncio.run(exercise())

    assert fake.render_calls == 2
    assert fake.close_calls == 1
    assert owner.physical_call_count == 2
    assert owner.lease_count == 2
    assert owner.closed_lease_count == 2


def test_fixture_denominator_uses_feasible_intent_variants_not_raw_measurements(
    tmp_path: Path,
) -> None:
    dataset = load_v2_dataset_manifest(
        MANIFEST,
        benchmark_root=ROOT / "benchmarks",
        gate_stage="v2_3_graph_conformance",
    )
    sample = next(
        item
        for item in dataset.manifest.split("development").samples
        if item.case_id == "arc_highlight_orb"
    )
    run_id = "fixture-feasible-denominator"
    store = LocalArtifactStore(tmp_path / "artifacts")
    catalog = LocalArtifactCatalog(
        store.register_run("fixture-test", run_id), run_id=run_id
    )
    bundle = measure_target_v2(
        dataset.resolve_image(sample).read_bytes(), catalog=catalog, run_id=run_id
    )
    assert len(bundle.measurements.target_hypotheses) == 3

    factory = runner._FixtureFactory(  # noqa: SLF001
        dataset=dataset, sample=sample, run_id=run_id
    )
    fixture = factory(bundle, catalog)

    assert fixture.request_constraint_set.target_sha256 == sample.sha256
    assert factory.source_hypothesis_count == 3
    assert factory.expected_hypothesis_count == 1
    assert factory.rejected_hypothesis_count == 2
    assert factory.rejection_reason_counts
    policy_ref = next(
        ref
        for ref in catalog.list_refs()
        if ref.kind == "v2_3_rendered_structure_fixture_policy"
    )
    assert policy_ref.schema_version == "v2_3_rendered_structure_fixture_policy_v2"
    policy = json.loads(catalog.read_bytes(policy_ref.artifact_id))
    assert policy["hypothesis_denominator_policy"] == (
        "build_intent_variants_feasible_variants_v1"
    )
    assert policy["source_hypothesis_count"] == 3
    assert policy["feasible_variant_count"] == 1
    assert policy["rejected_hypothesis_count"] == 2


def test_service_failure_still_collects_and_persists_case_denominator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = load_v2_dataset_manifest(
        MANIFEST,
        benchmark_root=ROOT / "benchmarks",
        gate_stage="v2_3_graph_conformance",
    )
    gate = evaluate_v2_dataset_stage_gate(dataset, stage="v2_3_graph_conformance")
    sample = next(
        item
        for item in dataset.manifest.split("development").samples
        if item.dataset_role == "regression"
        and item.source_suite_id == "png_to_shader_v1_m0"
    )
    source = dataset.resolve_image(sample).read_bytes()
    calls: list[object] = []

    class FailingService:
        async def invoke(self, **kwargs: object) -> None:
            calls.append(kwargs)
            raise RuntimeError("synthetic service failure")

    class FakeOutcome:
        def model_dump(self, *, mode: str) -> dict[str, object]:
            assert mode == "json"
            return {"case_id": sample.case_id, "success": False}

    async def fake_collect(**kwargs: object) -> object:
        calls.append(kwargs)
        return SimpleNamespace(
            capability=SimpleNamespace(outcome=FakeOutcome()), receipts=()
        )

    monkeypatch.setattr(
        runner,
        "create_png_to_shader_v2_development_service",
        lambda **_kwargs: FailingService(),
    )
    monkeypatch.setattr(runner, "collect_v2_3_verified_rendered_case", fake_collect)
    owner = runner._SharedSuiteRenderer()  # noqa: SLF001

    result = asyncio.run(
        runner._run_case_async(  # noqa: SLF001
            case_root=tmp_path / "case",
            dataset=dataset,
            sample=sample,
            split="development",
            source_bytes=source,
            suite_run_id="failure-suite",
            run_id="v2-actual-failure-case",
            case_budget=_budget(),
            graph_renderer=owner,
            replay_runner=V2_3ActualChromiumReplayRunner(),
            gate=gate,
            config_sha256="a" * 64,
            threshold_policy_hash="b" * 64,
            input_intent_outcomes_sha256="c" * 64,
            input_compiler_outcomes_sha256="d" * 64,
        )
    )

    assert not result.capability.outcome.model_dump(mode="json")["success"]
    assert len(calls) == 2
    receipts = json.loads(
        (tmp_path / "case/actual-replay-receipts.json").read_text(encoding="utf-8")
    )
    assert receipts["service_error_type"] == "RuntimeError"
    assert receipts["receipts"] == []
    assert json.loads((tmp_path / "case/outcome.json").read_text(encoding="utf-8")) == {
        "case_id": sample.case_id,
        "success": False,
    }


def test_render_call_accounting_closes_graph_and_successful_replay() -> None:
    collection = SimpleNamespace(
        capability=SimpleNamespace(
            outcome=SimpleNamespace(
                split="development",
                case_id="case-a",
                success=True,
                nominal_render_request_count=8,
            )
        ),
        receipts=(SimpleNamespace(item_receipts=tuple(range(8))),),
    )

    assert runner._validate_render_call_accounting(  # noqa: SLF001
        ordered_collections=cast(Any, (collection,)),
        graph_call_counts=(
            runner._CaseGraphCallCount(  # noqa: SLF001
                split="development", case_id="case-a", physical_call_count=8
            ),
        ),
        graph_observed_total=8,
        case_graph_limit=8,
        case_replay_limit=8,
    ) == (8, 8, 8)


def test_render_call_accounting_rejects_budget_and_receipt_drift() -> None:
    collection = SimpleNamespace(
        capability=SimpleNamespace(
            outcome=SimpleNamespace(
                split="development",
                case_id="case-a",
                success=True,
                nominal_render_request_count=8,
            )
        ),
        receipts=(SimpleNamespace(item_receipts=tuple(range(7))),),
    )
    graph_counts = (
        runner._CaseGraphCallCount(  # noqa: SLF001
            split="development", case_id="case-a", physical_call_count=8
        ),
    )

    with pytest.raises(ValueError, match="replay receipts"):
        runner._validate_render_call_accounting(  # noqa: SLF001
            ordered_collections=cast(Any, (collection,)),
            graph_call_counts=graph_counts,
            graph_observed_total=8,
            case_graph_limit=8,
            case_replay_limit=8,
        )
    with pytest.raises(ValueError, match="Graph physical render"):
        runner._validate_render_call_accounting(  # noqa: SLF001
            ordered_collections=cast(Any, (collection,)),
            graph_call_counts=graph_counts,
            graph_observed_total=8,
            case_graph_limit=7,
            case_replay_limit=8,
        )
