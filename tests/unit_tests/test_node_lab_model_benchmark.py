from __future__ import annotations

import asyncio
import json
from argparse import Namespace
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent.app.contracts.llm import (
    LLMCallOptions,
    LLMInvocationError,
    LLMResponse,
    TokenUsage,
)
from agent.app.services.node_lab_model_benchmark import (
    BudgetedModelGateway,
    ModelBenchmarkBudgets,
    load_model_benchmark_manifest,
    run_model_benchmark,
)
from scripts import run_node_lab_model_benchmark as model_benchmark_cli


class FixtureSequenceGateway:
    """按生产 System Prompt 选择合法 Fixture；不会访问网络。"""

    def __init__(
        self,
        suite: Any,
        *,
        cancel_on_call: int | None = None,
        fail_on_call: int | None = None,
        timeout_on_call: int | None = None,
        repair_first_call: bool = False,
    ) -> None:
        self._suite = suite
        self._cancel_on_call = cancel_on_call
        self._fail_on_call = fail_on_call
        self._timeout_on_call = timeout_on_call
        self._repair_first_call = repair_first_call
        self._pending_repair_fixture_id: str | None = None
        self.calls: list[tuple[Any, Any]] = []
        self._prompt_to_fixture = {
            case.prompt_sha256: case.response_fixture_id
            for case in suite.manifest.cases
        }

    async def ainvoke(self, messages: Any, options: Any) -> LLMResponse:
        self.calls.append((messages, options))
        if self._cancel_on_call == len(self.calls):
            raise asyncio.CancelledError
        if self._fail_on_call == len(self.calls):
            raise LLMInvocationError(
                "fixture provider failure",
                model_ref=options.model_ref,
                provider="fixture",
                retryable=False,
            )
        if self._timeout_on_call == len(self.calls):
            raise TimeoutError("fixture provider timeout")
        system_prompt = next(
            str(message.content)
            for message in messages
            if isinstance(message, SystemMessage)
        )
        from hashlib import sha256

        prompt_sha256 = sha256(system_prompt.encode("utf-8")).hexdigest()
        if prompt_sha256 not in self._prompt_to_fixture:
            if self._pending_repair_fixture_id is None:
                raise AssertionError("unexpected repair call")
            fixture_id = self._pending_repair_fixture_id
            self._pending_repair_fixture_id = None
        else:
            fixture_id = self._prompt_to_fixture[prompt_sha256]
            if self._repair_first_call and len(self.calls) == 1:
                self._pending_repair_fixture_id = fixture_id
                raw = {}
                text = json.dumps(raw)
                return LLMResponse(
                    message=AIMessage(content=text),
                    text=text,
                    reasoning_content=None,
                    model_ref="fake:model-actual",
                    requested_model_ref=options.model_ref,
                    model_identity_source="response_metadata",
                    latency_ms=5,
                    usage=TokenUsage(
                        input_tokens=10,
                        output_tokens=20,
                        total_tokens=30,
                    ),
                )
        raw = self._suite.fixtures[fixture_id]["responses"][0]["raw_output"]
        text = json.dumps(
            raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return LLMResponse(
            message=AIMessage(content=text),
            text=text,
            reasoning_content=None,
            model_ref="fake:model-actual",
            requested_model_ref=options.model_ref,
            model_identity_source="response_metadata",
            latency_ms=5,
            usage=TokenUsage(input_tokens=10, output_tokens=20, total_tokens=30),
        )


def test_manifest_freezes_five_roles_prompts_fixtures_and_all_budgets() -> None:
    suite = load_model_benchmark_manifest()

    assert [case.node_id for case in suite.manifest.cases] == [
        "visual_analysis",
        "author_initial",
        "author_compile_repair",
        "visual_critic",
        "author_visual_refine",
    ]
    assert len(suite.fixture_hashes) >= 5
    assert all(case.prompt_sha256 for case in suite.manifest.cases)
    budgets = suite.manifest.budgets
    assert budgets.max_semantic_calls >= 5
    assert budgets.max_json_repair_calls >= 5
    assert budgets.max_output_tokens_per_call > 0
    assert budgets.max_total_tokens > 0
    assert budgets.max_wall_time_seconds > 0
    assert budgets.max_estimated_cost_usd > 0
    assert suite.manifest.model_call_config.requested_model_ref == (
        "dashscope:qwen3.7-plus"
    )
    assert budgets.price_version == "manual-upper-bound-2026-07-15-v1"


def test_default_fixture_run_is_offline_atomic_and_self_contained(
    tmp_path: Path,
) -> None:
    suite = load_model_benchmark_manifest()
    output_root = tmp_path / "node-lab-model-output"

    report = asyncio.run(
        run_model_benchmark(
            suite,
            output_root=output_root,
            lab_root=tmp_path / "lab-runs",
            suite_run_id="fixture-suite",
        )
    )

    assert report["execution_mode"] == "fixture"
    assert report["planned_attempt_count"] == 5
    assert report["attempt_count"] == 5
    assert report["passed_attempt_count"] == 5
    assert report["failed_attempt_count"] == 0
    assert report["usage"]["semantic"]["call_count"] == 5
    run_root = output_root / "fixture-suite"
    assert (run_root / "config.json").is_file()
    assert (run_root / "environment.json").is_file()
    assert (run_root / "manifest.snapshot.yaml").is_file()
    assert (run_root / "report.json").is_file()
    assert (run_root / "report.md").is_file()
    assert len(list(run_root.glob("cases/*/attempts/*/execution.json"))) == 5
    assert list(run_root.rglob("*.tmp")) == []
    config = json.loads((run_root / "config.json").read_bytes())
    assert config["fixture_hashes"] == suite.fixture_hashes
    assert set(config["prompt_hashes"]) == {
        "visual_analysis",
        "author_initial",
        "author_compile_repair",
        "visual_critic",
        "author_visual_refine",
    }
    for attempt_path in run_root.glob("cases/*/attempts/*/execution.json"):
        attempt = json.loads(attempt_path.read_bytes())
        assert attempt["correctness_passed"] is True
        assert attempt["artifact_evidence"]
        assert attempt["response"]["execution_status"] == "completed"


@pytest.mark.parametrize(
    ("allow_model_calls", "real_model_enabled"),
    [(False, False), (True, False), (False, True)],
)
def test_real_mode_missing_any_gate_fails_before_gateway(
    tmp_path: Path,
    allow_model_calls: bool,
    real_model_enabled: bool,
) -> None:
    suite = load_model_benchmark_manifest()
    gateway = FixtureSequenceGateway(suite)
    output_root = tmp_path / "must-not-exist"

    with pytest.raises(ValueError, match="真实模型模式未同时满足"):
        asyncio.run(
            run_model_benchmark(
                suite,
                output_root=output_root,
                lab_root=tmp_path / "lab-runs",
                execution_mode="real",
                allow_model_calls=allow_model_calls,
                real_model_enabled=real_model_enabled,
                gateway=gateway,
            )
        )

    assert gateway.calls == []
    assert not output_root.exists()


def test_real_allowed_path_uses_only_injected_fake_gateway_and_splits_usage(
    tmp_path: Path,
) -> None:
    suite = load_model_benchmark_manifest()
    gateway = FixtureSequenceGateway(suite)

    report = asyncio.run(
        run_model_benchmark(
            suite,
            output_root=tmp_path / "output",
            lab_root=tmp_path / "lab-runs",
            suite_run_id="fake-real-suite",
            execution_mode="real",
            allow_model_calls=True,
            real_model_enabled=True,
            gateway=gateway,
        )
    )

    assert len(gateway.calls) == 5
    assert report["passed_attempt_count"] == 5
    assert report["usage"]["semantic"] == {
        "call_count": 5,
        "input_tokens": 50,
        "output_tokens": 100,
        "total_tokens": 150,
        "model_latency_ms": 25,
        "estimated_cost_usd": 0.0035,
    }
    assert report["usage"]["json_repair"]["call_count"] == 0
    assert report["diagnostics"]["parse_pass_rate"] == 1.0
    assert report["diagnostics"]["model_latency_ms"] == 25
    assert report["diagnostics"]["model_identity"] == {
        "manifest_requested_model_ref": "dashscope:qwen3.7-plus",
        "requested_model_ref_counts": {"dashscope:qwen3.7-plus": 5},
        "actual_model_ref_counts": {"fake:model-actual": 5},
        "missing_actual_model_call_count": 0,
    }
    assert all(
        options.max_output_tokens == suite.manifest.budgets.max_output_tokens_per_call
        and options.model_ref == suite.manifest.model_call_config.requested_model_ref
        for _messages, options in gateway.calls
    )
    assert report["duration_ms"]["p95"] is None
    assert all(role["duration_ms"]["p95"] is None for role in report["roles"].values())


def test_hard_token_reservation_rejects_before_provider_call() -> None:
    class NeverCalledGateway:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, messages: Any, options: Any) -> LLMResponse:
            self.calls += 1
            raise AssertionError("provider must not be called")

    delegate = NeverCalledGateway()
    gateway = BudgetedModelGateway(
        delegate,
        ModelBenchmarkBudgets(
            max_semantic_calls=1,
            max_json_repair_calls=1,
            max_output_tokens_per_call=10,
            max_total_tokens=10,
            max_wall_time_seconds=10,
            max_estimated_cost_usd=0.001,
            input_cost_per_million_tokens_usd=1,
            output_cost_per_million_tokens_usd=1,
            price_version="test-v1",
        ),
        "openai:gpt-4.1",
    )

    with pytest.raises(LLMInvocationError, match="reservation_unavailable"):
        asyncio.run(
            gateway.ainvoke(
                [HumanMessage(content="input")],
                LLMCallOptions(model_ref="openai:gpt-4.1"),
            )
        )

    assert delegate.calls == 0


def test_interruption_is_atomic_and_stays_in_planned_denominator(
    tmp_path: Path,
) -> None:
    suite = load_model_benchmark_manifest()
    gateway = FixtureSequenceGateway(suite, cancel_on_call=3)
    output_root = tmp_path / "output"

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            run_model_benchmark(
                suite,
                output_root=output_root,
                lab_root=tmp_path / "lab-runs",
                suite_run_id="cancelled-suite",
                execution_mode="real",
                allow_model_calls=True,
                real_model_enabled=True,
                gateway=gateway,
            )
        )

    run_root = output_root / "cancelled-suite"
    report = json.loads((run_root / "report.json").read_bytes())
    assert report["planned_attempt_count"] == 5
    assert report["attempt_count"] == 3
    assert report["passed_attempt_count"] == 2
    assert report["interrupted_attempt_count"] == 1
    assert report["unstarted_attempt_count"] == 2
    assert report["failed_attempt_count"] == 3
    assert report["usage"]["semantic"]["call_count"] == 3
    assert not (
        run_root / "cases/author-compile-repair/attempts/attempt-001/execution.json"
    ).exists()
    interruptions = list(run_root.glob("cases/*/attempts/*/interruptions/*.json"))
    assert len(interruptions) == 1
    assert list(run_root.rglob("*.tmp")) == []

    resumed = asyncio.run(
        run_model_benchmark(
            suite,
            output_root=output_root,
            lab_root=tmp_path / "lab-runs",
            suite_run_id="cancelled-suite",
            execution_mode="real",
            allow_model_calls=True,
            real_model_enabled=True,
            gateway=gateway,
        )
    )

    assert resumed["planned_attempt_count"] == 5
    assert resumed["denominator_attempt_count"] == 6
    assert resumed["completed_attempt_count"] == 5
    assert resumed["interrupted_attempt_count"] == 1
    assert resumed["unstarted_attempt_count"] == 0
    assert resumed["passed_attempt_count"] == 5
    assert resumed["failed_attempt_count"] == 1
    assert resumed["usage"]["semantic"]["call_count"] == 6
    assert len(list(run_root.glob("cases/*/attempts/*/execution.json"))) == 5
    assert len(list(run_root.glob("cases/*/attempts/*/interruptions/*.json"))) == 1


def test_real_provider_failure_remains_in_denominator_and_other_roles_continue(
    tmp_path: Path,
) -> None:
    suite = load_model_benchmark_manifest()
    gateway = FixtureSequenceGateway(suite, fail_on_call=3)

    report = asyncio.run(
        run_model_benchmark(
            suite,
            output_root=tmp_path / "output",
            lab_root=tmp_path / "lab-runs",
            suite_run_id="failed-suite",
            execution_mode="real",
            allow_model_calls=True,
            real_model_enabled=True,
            gateway=gateway,
        )
    )

    assert len(gateway.calls) == 5
    assert report["planned_attempt_count"] == 5
    assert report["attempt_count"] == 5
    assert report["passed_attempt_count"] == 4
    assert report["failed_attempt_count"] == 1
    assert report["correctness_rate"] == 0.8


def test_real_provider_timeout_is_counted_in_report_diagnostics(
    tmp_path: Path,
) -> None:
    suite = load_model_benchmark_manifest()
    gateway = FixtureSequenceGateway(suite, timeout_on_call=3)

    report = asyncio.run(
        run_model_benchmark(
            suite,
            output_root=tmp_path / "output",
            lab_root=tmp_path / "lab-runs",
            suite_run_id="timeout-suite",
            execution_mode="real",
            allow_model_calls=True,
            real_model_enabled=True,
            gateway=gateway,
        )
    )

    assert report["failed_attempt_count"] == 1
    assert report["diagnostics"]["timeout_attempt_count"] == 1
    assert (
        report["roles"]["author_compile_repair"]["diagnostics"]["timeout_attempt_count"]
        == 1
    )


def test_report_aggregates_repair_schema_model_and_latency_diagnostics(
    tmp_path: Path,
) -> None:
    suite = load_model_benchmark_manifest()
    gateway = FixtureSequenceGateway(suite, repair_first_call=True)

    report = asyncio.run(
        run_model_benchmark(
            suite,
            output_root=tmp_path / "output",
            lab_root=tmp_path / "lab-runs",
            suite_run_id="repair-suite",
            execution_mode="real",
            allow_model_calls=True,
            real_model_enabled=True,
            gateway=gateway,
        )
    )

    assert report["passed_attempt_count"] == 5
    assert report["usage"]["semantic"]["call_count"] == 5
    assert report["usage"]["json_repair"]["call_count"] == 1
    assert report["diagnostics"]["parse_status_counts"] == {
        "invalid": 1,
        "valid": 5,
    }
    assert report["diagnostics"]["parse_pass_rate"] == pytest.approx(5 / 6)
    assert report["diagnostics"]["schema_issue_counts"]
    assert report["diagnostics"]["model_latency_ms"] == 30
    assert report["diagnostics"]["model_identity"]["actual_model_ref_counts"] == {
        "fake:model-actual": 6
    }


def test_cli_stdout_is_stable_summary_and_case_failure_is_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_run(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "suite_id": "node_lab_model_roles_v1",
            "suite_run_id": "cli-failed-suite",
            "failed_attempt_count": 1,
        }

    monkeypatch.setattr(model_benchmark_cli, "run_model_benchmark", fake_run)
    output_root = tmp_path / "output"
    args = Namespace(
        manifest=model_benchmark_cli.DEFAULT_MODEL_BENCHMARK_MANIFEST,
        output_root=output_root,
        lab_root=tmp_path / "lab",
        suite_run_id="cli-failed-suite",
        execution_mode="fixture",
        allow_model_calls=False,
        validate_only=False,
        require_passed=False,
    )

    exit_code = asyncio.run(model_benchmark_cli._run(args))

    assert exit_code == model_benchmark_cli.EXIT_CASE_FAILED
    assert json.loads(capsys.readouterr().out) == {
        "suite_id": "node_lab_model_roles_v1",
        "suite_run_id": "cli-failed-suite",
        "status": "failed",
        "report_path": str(output_root.resolve() / "cli-failed-suite" / "report.json"),
    }
