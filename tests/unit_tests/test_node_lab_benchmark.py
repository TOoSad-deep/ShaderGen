from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from agent.app.lab.benchmark import (
    BenchmarkExpectation,
    ValidatedBenchmarkSuite,
    compare_benchmark_reports,
    load_benchmark_manifest,
    run_benchmark_suite,
    source_environment,
)
from agent.app.lab.runner import NodeLabApplication
from agent.app.lab.store import NodeLabStore
from agent.app.services.node_lab import create_node_lab_application
from shaderforge.rendering import CompileResult, RenderResult
from shaderforge.validation import validate_shader

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "benchmarks/node_lab/png_to_shader_v1/manifest.yaml"
SCENARIO_MANIFEST = ROOT / "benchmarks/node_lab/png_to_shader_v1/scenario-manifest.yaml"
WARM_MANIFEST = (
    ROOT / "benchmarks/node_lab/png_to_shader_v1/renderer-warm-manifest.yaml"
)
REFERENCE = ROOT / "benchmarks/png_to_shader_v1/images/solid_circle.png"
FIXED_NOW = datetime(2026, 7, 14, 10, 0, tzinfo=timezone.utc)


class SequentialIds:
    def __init__(self) -> None:
        self._value = 0

    def __call__(self) -> str:
        self._value += 1
        return f"benchmark-id-{self._value}"


class FakeRenderer:
    def __init__(self, image: bytes) -> None:
        self._image = image
        self.render_count = 0
        self.closed = False

    async def render(
        self, fragment_source: str, width: int, height: int
    ) -> RenderResult:
        self.render_count += 1
        validation = validate_shader(fragment_source)
        return RenderResult(
            success=validation.valid,
            image_bytes=self._image if validation.valid else None,
            width=width,
            height=height,
            compile=CompileResult(
                success=validation.valid,
                vertex_log="",
                fragment_log="",
                link_log="",
                draw_error=None if validation.valid else "static_validation_failed",
                static_validation=validation,
            ),
            console_errors=(),
            metadata=None,
            duration_ms=2.0,
        )

    async def close(self) -> None:
        self.closed = True


def _application(tmp_path: Path) -> NodeLabApplication:
    image = REFERENCE.read_bytes()
    return create_node_lab_application(
        root=tmp_path / "lab",
        renderer_factory=lambda: FakeRenderer(image),
    )


def _suite(application: NodeLabApplication) -> ValidatedBenchmarkSuite:
    ids = {item.capability_id for item in application.describe_capabilities()}
    node_ids = {item.node_id for item in application.describe_nodes()}
    return load_benchmark_manifest(
        MANIFEST,
        capability_ids=ids,
        node_ids=node_ids,
    )


def test_manifest_freezes_capabilities_files_and_hashes(tmp_path: Path) -> None:
    application = _application(tmp_path)
    suite = _suite(application)

    assert suite.manifest.suite_id == "node_lab_ai_off_v1"
    assert len(suite.manifest.cases) == 8
    assert suite.summary()["profiles"] == ["micro", "node", "renderer_cold"]
    node_case = next(case for case in suite.manifest.cases if case.profile == "node")
    assert node_case.target_type == "node"
    assert node_case.node_id == "decide_after_render"
    assert node_case.capability_id is None
    assert all(path.is_file() for path in suite.artifact_paths.values())


def test_source_fingerprint_covers_deterministic_and_production_sources(
    tmp_path: Path,
) -> None:
    application = _application(tmp_path)
    environment, source_fingerprint, environment_fingerprint = source_environment(
        extra_source_paths=application.benchmark_source_paths(),
    )
    hashes = environment["source_hashes"]

    assert "src/agent/app/nodes/integrations/node_lab/deterministic.py" in hashes
    assert "src/agent/app/nodes/integrations/node_lab/registry.py" in hashes
    assert "src/agent/app/nodes/png_to_shader_v1/runtime.py" in hashes
    assert "src/agent/app/nodes/png_to_shader_v1/render_evaluate.py" in hashes
    assert "src/agent/app/prompts/shader_author_initial_v1.yaml" in hashes
    assert "src/agent/app/parsers/png_to_shader_v1.py" in hashes
    assert len(source_fingerprint) == 64
    assert len(environment_fingerprint) == 64


@pytest.mark.anyio
async def test_node_target_uses_execute_step_not_capability_profile(
    tmp_path: Path,
) -> None:
    application = _application(tmp_path)
    suite = _suite(application)
    node_case = next(
        case for case in suite.manifest.cases if case.target_type == "node"
    )
    node_suite = ValidatedBenchmarkSuite(
        manifest=suite.manifest.model_copy(update={"cases": [node_case]}),
        manifest_path=suite.manifest_path,
        manifest_sha256="d" * 64,
        artifact_paths={},
    )

    class StepOnlyProbe:
        def __init__(self, wrapped: NodeLabApplication) -> None:
            self.wrapped = wrapped
            self.step_calls = 0

        def __getattr__(self, name: str) -> Any:
            return getattr(self.wrapped, name)

        async def execute_step(self, request: Any) -> Any:
            self.step_calls += 1
            return await self.wrapped.execute_step(request)

        async def execute_capability(self, request: Any) -> Any:
            del request
            raise AssertionError("node target 不得退化为 capability 调用")

    probe = StepOnlyProbe(application)
    await run_benchmark_suite(
        probe,
        node_suite,
        output_root=tmp_path / "output",
        suite_run_id="node-target-suite",
    )
    execution = json.loads(
        (
            tmp_path / "output/node-target-suite/cases/route_successful_render/"
            "attempts/attempt-001/execution.json"
        ).read_bytes()
    )

    assert probe.step_calls == 1
    assert execution["target_type"] == "node"
    assert execution["node_id"] == "decide_after_render"
    assert execution["response"]["schema_version"] == ("node_lab_execution_response_v1")


@pytest.mark.anyio
async def test_runner_preserves_attempts_reports_and_exact_resume(
    tmp_path: Path,
) -> None:
    application = _application(tmp_path)
    suite = _suite(application)
    output = tmp_path / "output"

    first = await run_benchmark_suite(
        application,
        suite,
        output_root=output,
        suite_run_id="suite-1",
    )
    second = await run_benchmark_suite(
        application,
        suite,
        output_root=output,
        suite_run_id="suite-1",
    )

    assert first == second
    assert first["schema_version"] == "node_lab_benchmark_report_v1"
    assert "gate" not in first
    assert first["attempt_count"] == 8
    assert first["failed_attempt_count"] == 0
    assert first["correctness_rate"] == 1.0
    assert first["duration_ms"]["p95"] is None
    assert (output / "suite-1/config.json").is_file()
    assert (output / "suite-1/manifest.snapshot.yaml").is_file()
    assert (output / "suite-1/report.json").is_file()
    assert (
        len(list((output / "suite-1/cases").glob("*/attempts/*/execution.json"))) == 8
    )
    attempt = json.loads(
        (
            output
            / "suite-1/cases/measure_solid_circle/attempts/attempt-001/execution.json"
        ).read_bytes()
    )
    assert {item["role"] for item in attempt["artifact_evidence"]} == {
        "input",
        "output",
    }
    for item in attempt["artifact_evidence"]:
        payload = output / "suite-1" / item["relative_path"]
        assert payload.is_file()
        assert sha256(payload.read_bytes()).hexdigest() == item["descriptor"]["sha256"]

    changed = replace(suite, manifest_sha256="f" * 64)
    with pytest.raises(ValueError, match="config hash"):
        await run_benchmark_suite(
            application,
            changed,
            output_root=output,
            suite_run_id="suite-1",
        )


@pytest.mark.anyio
async def test_failures_remain_in_denominator_and_comparison_checks_fingerprints(
    tmp_path: Path,
) -> None:
    application = _application(tmp_path)
    suite = _suite(application)
    first_case = suite.manifest.cases[0].model_copy(
        update={"expect": BenchmarkExpectation(outcome="rejected")}
    )
    failing_manifest = suite.manifest.model_copy(update={"cases": [first_case]})
    failing_suite = ValidatedBenchmarkSuite(
        manifest=failing_manifest,
        manifest_path=suite.manifest_path,
        manifest_sha256="e" * 64,
        artifact_paths={
            key: value
            for key, value in suite.artifact_paths.items()
            if key[0] == first_case.case_id
        },
    )
    output = tmp_path / "output"
    report = await run_benchmark_suite(
        application,
        failing_suite,
        output_root=output,
        suite_run_id="suite-failed",
    )

    assert report["attempt_count"] == 1
    assert report["failed_attempt_count"] == 1
    assert report["correctness_rate"] == 0.0
    assert report["failed_attempts"] == ["normalize_solid_circle:attempt-001"]

    report_path = output / "suite-failed/report.json"
    comparable = compare_benchmark_reports(report_path, report_path)
    assert comparable["status"] == "comparable"
    assert comparable["correctness_rate_delta"] == 0.0

    drifted_path = tmp_path / "drifted-report.json"
    drifted = json.loads(report_path.read_bytes())
    drifted["environment_fingerprint"] = "0" * 64
    drifted_path.write_text(json.dumps(drifted), encoding="utf-8")
    non_comparable = compare_benchmark_reports(report_path, drifted_path)
    assert non_comparable["status"] == "non_comparable"
    assert "correctness_rate_delta" not in non_comparable


@pytest.mark.anyio
async def test_scenario_resolves_step_bindings_and_preserves_all_evidence(
    tmp_path: Path,
) -> None:
    image = REFERENCE.read_bytes()
    application = create_node_lab_application(
        root=tmp_path / "lab",
        renderer_factory=lambda: FakeRenderer(image),
    )
    capability_ids = {
        item.capability_id for item in application.describe_capabilities()
    }
    suite = load_benchmark_manifest(
        SCENARIO_MANIFEST,
        capability_ids=capability_ids,
        node_ids={item.node_id for item in application.describe_nodes()},
    )

    report = await run_benchmark_suite(
        application,
        suite,
        output_root=tmp_path / "output",
        suite_run_id="scenario-suite",
    )
    execution = json.loads(
        (
            tmp_path / "output/scenario-suite/cases/pink_gel_fact_layer_scenario/"
            "attempts/attempt-001/execution.json"
        ).read_bytes()
    )

    assert report["attempt_count"] == 2
    assert report["correctness_rate"] == 1.0
    assert report["profiles"] == ["pipeline", "scenario"]
    assert [item["step_id"] for item in execution["responses"]] == [
        "normalize",
        "measure",
        "validate",
        "render",
        "evaluate",
    ]
    assert execution["response"]["output"]["score"]["metric_version"] == (
        "basic_oracle_v1"
    )
    assert {item["step_id"] for item in execution["artifact_evidence"]} >= {
        "normalize",
        "measure",
        "render",
        "evaluate",
    }

    pipeline = json.loads(
        (
            tmp_path
            / "output/scenario-suite/cases/deterministic_run_lifecycle_pipeline/"
            "attempts/attempt-001/execution.json"
        ).read_bytes()
    )
    assert pipeline["target_type"] == "pipeline"
    assert [item["node_id"] for item in pipeline["responses"]] == [
        "initialize_run",
        "measure_target",
        "prepare_context",
        "finalize",
    ]
    by_manifest_id = {item["step_id"]: item for item in pipeline["responses"]}
    measure_actual = by_manifest_id["measure"]["response"]["step_id"]
    assert by_manifest_id["prepare_context"]["manifest_base_step_id"] == "measure"
    assert by_manifest_id["prepare_context"]["response"]["base_step_id"] == (
        measure_actual
    )
    assert by_manifest_id["finalize_empty_branch"]["manifest_base_step_id"] == (
        "measure"
    )
    assert by_manifest_id["finalize_empty_branch"]["response"]["base_step_id"] == (
        measure_actual
    )
    assert pipeline["response"]["outcome"] == "stopped"
    assert pipeline["response"]["output"]["final_result"]["success"] is False


@pytest.mark.anyio
async def test_warm_renderer_is_reused_and_warmup_is_excluded(
    tmp_path: Path,
) -> None:
    image = REFERENCE.read_bytes()
    renderers: list[FakeRenderer] = []

    def factory() -> FakeRenderer:
        renderer = FakeRenderer(image)
        renderers.append(renderer)
        return renderer

    application = NodeLabApplication(
        store=NodeLabStore(tmp_path / "lab"),
        renderer_factory=factory,
        id_factory=SequentialIds(),
        now=lambda: FIXED_NOW,
    )
    suite = load_benchmark_manifest(
        WARM_MANIFEST,
        capability_ids={
            item.capability_id for item in application.describe_capabilities()
        },
        node_ids=set(),
    )

    report = await run_benchmark_suite(
        application,
        suite,
        output_root=tmp_path / "output",
        suite_run_id="warm-suite",
    )

    assert report["attempt_count"] == 20
    assert report["profiles"] == ["renderer_warm"]
    assert report["renderer_lifecycle"] == "warm_per_suite"
    assert report["duration_ms"]["p95"] is not None
    assert len(renderers) == 1
    assert renderers[0].render_count == 21
    assert renderers[0].closed is True
    warmup = json.loads(
        (
            tmp_path / "output/warm-suite/cases/render_pink_gel_warm/"
            "attempts/warmup-001/execution.json"
        ).read_bytes()
    )
    measured = json.loads(
        (
            tmp_path / "output/warm-suite/cases/render_pink_gel_warm/"
            "attempts/attempt-001/execution.json"
        ).read_bytes()
    )
    assert warmup["response"]["usage"]["browser_launch_count"] == 1
    assert measured["response"]["usage"]["browser_launch_count"] == 0
    assert measured["response"]["provenance"]["renderer_lifecycle"] == (
        "warm_per_suite"
    )


@pytest.mark.anyio
async def test_interruption_is_preserved_and_exact_resume_does_not_overwrite_it(
    tmp_path: Path,
) -> None:
    application = _application(tmp_path)
    suite = _suite(application)
    first_case = suite.manifest.cases[0]
    one_case_suite = ValidatedBenchmarkSuite(
        manifest=suite.manifest.model_copy(update={"cases": [first_case]}),
        manifest_path=suite.manifest_path,
        manifest_sha256="c" * 64,
        artifact_paths={
            key: value
            for key, value in suite.artifact_paths.items()
            if key[0] == first_case.case_id
        },
    )

    class InterruptOnce:
        def __init__(self, wrapped: NodeLabApplication) -> None:
            self.wrapped = wrapped
            self.interrupted = False

        def __getattr__(self, name: str):
            return getattr(self.wrapped, name)

        async def execute_capability(self, request):
            if not self.interrupted:
                self.interrupted = True
                raise asyncio.CancelledError
            return await self.wrapped.execute_capability(request)

    output = tmp_path / "output"
    with pytest.raises(asyncio.CancelledError):
        await run_benchmark_suite(
            InterruptOnce(application),
            one_case_suite,
            output_root=output,
            suite_run_id="interrupted-suite",
        )

    interruption = (
        output / "interrupted-suite/cases/normalize_solid_circle/attempts/attempt-001/"
        "interruptions/interruption-001.json"
    )
    assert interruption.is_file()
    resumed = await run_benchmark_suite(
        application,
        one_case_suite,
        output_root=output,
        suite_run_id="interrupted-suite",
    )
    exact = await run_benchmark_suite(
        application,
        one_case_suite,
        output_root=output,
        suite_run_id="interrupted-suite",
    )

    assert resumed == exact
    assert resumed["attempt_count"] == 2
    assert resumed["completed_attempt_count"] == 1
    assert resumed["interrupted_attempt_count"] == 1
    assert resumed["passed_attempt_count"] == 1
    assert resumed["failed_attempt_count"] == 1
    assert resumed["correctness_rate"] == 0.5
    assert interruption.is_file()
