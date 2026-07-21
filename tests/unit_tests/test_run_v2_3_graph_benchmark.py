from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

import scripts.run_v2_3_graph_benchmark as runner
from agent.app.states.png_to_shader_v2_state_store import (
    LocalPngToShaderV2StateStore,
)
from shaderforge.benchmark import (
    evaluate_v2_dataset_stage_gate,
    load_v2_dataset_manifest,
)
from shaderforge.benchmark.v2_3_graph_gate import V2_3_RESTART_PHASES
from shaderforge.contracts import canonical_sha256
from shaderforge.store import LocalArtifactCatalog, LocalArtifactStore
from tests.fixtures.png_to_shader_v2_contracts import artifact_ref, make_state


def test_graph_invoke_explicitly_disables_langsmith_tracing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracing_values: list[bool | None] = []
    invocation_configs: list[dict[str, object]] = []

    @contextmanager
    def fake_tracing_context(*, enabled: bool | None) -> Iterator[None]:
        tracing_values.append(enabled)
        yield

    class FakeGraph:
        async def ainvoke(
            self, state: object, *, config: dict[str, object]
        ) -> dict[str, Any]:
            invocation_configs.append(config)
            restored = make_state()
            return {
                name: getattr(restored, name) for name in type(restored).model_fields
            }

    monkeypatch.setattr(runner, "tracing_context", fake_tracing_context)

    asyncio.run(runner._invoke_graph(FakeGraph(), make_state()))  # noqa: SLF001

    assert tracing_values == [False]
    assert invocation_configs == [{"callbacks": []}]


def test_rendered_restart_phase_requires_committed_multi_capture_closure() -> None:
    base = make_state()
    rendered = base.model_copy(
        update={
            "phase": "rendering",
            "active_attempt_id": "attempt-1",
            "active_compilation_ref": artifact_ref(
                "compilation", "1", kind="compilation"
            ),
            "active_diagnostic_compilation_ref": artifact_ref(
                "diagnostic-compilation", "2", kind="diagnostic_compilation"
            ),
            "active_render_plan_ref": artifact_ref(
                "render-plan", "3", kind="render_plan"
            ),
            "active_render_progress_ref": artifact_ref(
                "render-progress", "4", kind="render_progress"
            ),
            "active_render_repeatability_ref": artifact_ref(
                "render-repeatability", "5", kind="render_repeatability"
            ),
        }
    )

    assert runner._matches_restart_phase(rendered, "rendered")  # noqa: SLF001
    assert not runner._matches_restart_phase(  # noqa: SLF001
        rendered.model_copy(update={"active_render_repeatability_ref": None}),
        "rendered",
    )

    reserved = rendered.budget_state.model_copy(
        update={
            "reserved": rendered.budget_state.reserved.model_copy(
                update={"render_calls": 1}
            )
        }
    )
    evidence_before_commit = rendered.model_copy(
        update={
            "active_render_call_ordinal": 1,
            "budget_state": reserved,
        }
    )
    assert not runner._matches_restart_phase(  # noqa: SLF001
        evidence_before_commit,
        "rendered",
    )


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "benchmarks/png_to_shader_v2/dataset_manifest.v1.json"


def test_one_case_uses_production_graph_with_restart_replay_and_cas(
    tmp_path: Path,
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
    config_payload: dict[str, object] = {
        "schema_version": "v2_3_graph_benchmark_config_v2",
        "execution_mode": "fixture/no-model",
        "production_admission_enabled": False,
        "langsmith_tracing_enabled": False,
    }
    config_sha256 = canonical_sha256(config_payload)
    config = {**config_payload, "config_sha256": config_sha256}

    outcome = runner._run_case(  # noqa: SLF001
        output=tmp_path,
        dataset=dataset,
        gate=gate,
        sample=sample,
        split="development",
        config=config,
        config_sha256=config_sha256,
        input_intent_outcomes_sha256="d" * 64,
        input_compiler_outcomes_sha256="e" * 64,
        restart_phases=V2_3_RESTART_PHASES,
    )

    assert outcome.success
    assert outcome.failure_code is None
    assert outcome.terminal_phase == "finalized"
    assert outcome.expected_seed_attempt_count == outcome.hypothesis_count * 3
    assert outcome.seed_attempt_count == outcome.expected_seed_attempt_count
    assert outcome.attempt_artifact_closure_count == outcome.expected_seed_attempt_count
    assert outcome.hypothesis_identity_propagated
    assert tuple(item.phase for item in outcome.restart_phase_results) == (
        V2_3_RESTART_PHASES
    )
    assert all(item.verified for item in outcome.restart_phase_results)
    assert outcome.deterministic_replay_verified
    assert outcome.cas_stale_write_rejected
    assert outcome.model_calls == 0
    assert outcome.production_admission_enabled is False
    assert (tmp_path / "cases/development" / sample.case_id / "outcome.json").is_file()

    run_id = f"v2-3-development-{sample.case_id}"
    case_root = tmp_path / "cases/development" / sample.case_id
    catalog = LocalArtifactCatalog(
        LocalArtifactStore(case_root / "artifact-store").resolve_run(run_id),
        run_id=run_id,
    )
    final = LocalPngToShaderV2StateStore(
        case_root / "state-store"
    ).load_last_confirmed(run_id)
    closure = runner._inspect_attempt_closure(final, catalog)  # noqa: SLF001
    assert closure[0] == outcome.expected_seed_attempt_count
    assert closure[3] is True

    duplicated = final.model_copy(
        update={
            "candidate_summary_refs": (
                *final.candidate_summary_refs,
                final.candidate_summary_refs[0],
            )
        }
    )
    with pytest.raises(ValueError, match="summary ref 不得重复"):
        runner._inspect_attempt_closure(duplicated, catalog)  # noqa: SLF001

    first_branch = final.hypothesis_branches[0]
    missing_expected = first_branch.model_copy(
        update={"seed_refs": first_branch.seed_refs[1:]}
    )
    wrong_binding = final.model_copy(
        update={
            "hypothesis_branches": (
                missing_expected,
                *final.hypothesis_branches[1:],
            )
        }
    )
    with pytest.raises(ValueError, match="错绑"):
        runner._inspect_attempt_closure(wrong_binding, catalog)  # noqa: SLF001

    candidate_ref = final.candidate_summary_refs[0]
    blob = (
        LocalArtifactStore(case_root / "artifact-store").resolve_run(run_id).root
        / ".artifact-catalog-v2/blobs"
        / f"{candidate_ref.artifact_id}.blob"
    )
    blob.write_bytes(blob.read_bytes() + b"\n")
    with pytest.raises(ValueError):
        runner._inspect_attempt_closure(final, catalog)  # noqa: SLF001


def test_runner_requires_exclusive_output_directory(tmp_path: Path) -> None:
    output = tmp_path / "already-exists"
    output.mkdir()

    with pytest.raises(FileExistsError):
        runner.run_v2_3_graph_benchmark(output)


def test_runner_rejects_incomplete_real_model_authorization(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="双显式开关"):
        runner.run_v2_3_graph_benchmark(
            tmp_path / "real",
            execution_mode="real",
            allow_model_calls=True,
            enable_real_model=False,
            model_call_budget=10,
        )

    with pytest.raises(ValueError, match="strict conformance runner"):
        runner.run_v2_3_graph_benchmark(
            tmp_path / "real-authorized",
            execution_mode="real",
            allow_model_calls=True,
            enable_real_model=True,
            model_call_budget=10,
        )


def test_fixture_mode_rejects_any_model_switch_or_budget(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="fixture/no-model"):
        runner.run_v2_3_graph_benchmark(
            tmp_path / "fixture",
            allow_model_calls=True,
            model_call_budget=1,
        )
