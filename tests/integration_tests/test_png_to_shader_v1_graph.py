from __future__ import annotations

import json
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langgraph.store.memory import InMemoryStore

from agent.app.contracts.llm import LLMInvocationError, LLMResponse
from agent.app.graphs.png_to_shader_v1_graph import build_png_to_shader_v1_graph
from agent.app.memory.store import list_project_memories
from shaderforge.contracts import AcceptancePolicy, BudgetPolicy, StopReason
from shaderforge.evaluation import ScoreBreakdownV1
from shaderforge.rendering import (
    CompileResult,
    PlaywrightWebGL1Renderer,
    RenderResult,
)
from shaderforge.store import LocalArtifactStore
from shaderforge.validation import validate_shader
from tests.fixtures.png_to_shader_v1_samples import (
    GOLDEN_GLSL,
    analysis_payload,
    author_payload,
    json_text,
    review_payload,
)

ROOT = Path(__file__).resolve().parents[2]
REFERENCE_IMAGE = ROOT / "benchmarks/png_to_shader_v1/images/pink_gel.png"
GOLDEN_SHADER = ROOT / "benchmarks/png_to_shader_v1/golden/pink_gel.frag"


def response(payload: dict) -> LLMResponse:
    text = json_text(payload)
    return LLMResponse(
        message=AIMessage(content=text),
        text=text,
        reasoning_content=None,
        model_ref="fake:quality-actual",
        requested_model_ref="fake:quality-requested",
        model_identity_source="response_metadata",
        latency_ms=5,
    )


class ScriptedGateway:
    def __init__(self, script: Iterable[LLMResponse | Exception]) -> None:
        self.script = list(script)
        self.calls = 0

    async def ainvoke(self, _messages, _options):
        self.calls += 1
        if not self.script:
            raise AssertionError("发生了未预期的额外模型调用")
        value = self.script.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class FakeRenderer:
    def __init__(self, outcomes: Iterable[bool]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[str] = []
        self.closed = False

    async def render(
        self, fragment_source: str, width: int, height: int
    ) -> RenderResult:
        self.calls.append(fragment_source)
        if not self.outcomes:
            raise AssertionError("发生了未预期的额外渲染")
        success = self.outcomes.pop(0)
        validation = validate_shader(fragment_source)
        compile_result = CompileResult(
            success=success,
            vertex_log="",
            fragment_log="" if success else "ERROR: synthetic compile failure",
            link_log="",
            draw_error=None if success else "program link failed",
            static_validation=validation,
        )
        image = f"synthetic-png-{len(self.calls)}".encode() if success else None
        return RenderResult(
            success=success,
            image_bytes=image,
            width=width,
            height=height,
            compile=compile_result,
            console_errors=(),
            metadata=None,
            duration_ms=1.0,
        )

    async def close(self) -> None:
        self.closed = True


class ScriptedEvaluator:
    def __init__(self, losses: Iterable[float]) -> None:
        self.losses = list(losses)

    def __call__(self, _reference, _candidate, *, measurements):
        if not self.losses:
            raise AssertionError("发生了未预期的额外评分")
        loss = self.losses.pop(0)
        return ScoreBreakdownV1(
            metric_version="scripted_oracle_v1",
            total_loss=loss,
            global_rmse=loss,
            global_mae=loss,
            edge_loss=loss,
            geometry_loss=loss,
            representative_pixel_loss=loss,
            roi_losses=(("highlight", loss),),
            protected_region_losses=(("protected_center", 0.10),),
            effective_weights=(("global_rmse", 1.0),),
            diagnostics=(),
        )


def budget(
    *,
    visual: int,
    compile_repairs: int,
    model_calls: int,
) -> BudgetPolicy:
    return BudgetPolicy(
        max_visual_refinements=visual,
        max_compile_repairs=compile_repairs,
        max_model_calls=model_calls,
        max_wall_time_seconds=30,
    )


def graph_input(run_id: str, policy: BudgetPolicy) -> dict:
    return {
        "project_id": "m3-tests",
        "run_id": run_id,
        "image": REFERENCE_IMAGE.read_bytes(),
        "content_type": "image/png",
        "quality_preset": "balanced",
        "budget_policy": asdict(policy),
        "acceptance_policy": asdict(AcceptancePolicy(quality_threshold=0.0)),
    }


async def run_scripted_graph(
    tmp_path: Path,
    *,
    run_id: str,
    gateway_script: Iterable[LLMResponse | Exception],
    render_outcomes: Iterable[bool],
    losses: Iterable[float],
    policy: BudgetPolicy,
    enable_measurement_seed: bool = False,
):
    gateway = ScriptedGateway(gateway_script)
    renderer = FakeRenderer(render_outcomes)
    memory_store = InMemoryStore()
    graph = build_png_to_shader_v1_graph(
        gateway,
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
        renderer_factory=lambda _replay: renderer,
        evaluator=ScriptedEvaluator(losses),
        enable_measurement_seed=enable_measurement_seed,
        store=memory_store,
    )
    result = await graph.ainvoke(graph_input(run_id, policy))
    return result, gateway, renderer, memory_store


@pytest.mark.anyio
async def test_measurement_seed_is_independent_once_only_candidate(
    tmp_path: Path,
) -> None:
    result, gateway, renderer, _memory_store = await run_scripted_graph(
        tmp_path,
        run_id="measurement-seed",
        gateway_script=[response(analysis_payload()), response(author_payload())],
        render_outcomes=[True, True],
        losses=[0.20, 0.10],
        policy=budget(visual=0, compile_repairs=0, model_calls=4),
        enable_measurement_seed=True,
    )

    records = result["candidate_records"]
    assert len(records) == 2
    assert records[0].origin == "model"
    assert records[1].origin == "deterministic"
    assert records[1].generator_version == "measurement_affine_seed_v1"
    assert records[1].parent_candidate_id is None
    assert result["measurement_seed_attempted"] is True
    assert result["current_best_id"] == "candidate-0002"
    assert result["no_improvement_count"] == 0
    assert result["visual_refinement_count"] == 0
    assert result["model_call_count"] == 2
    assert result["final_result"]["candidate_id"] == "candidate-0002"
    assert gateway.calls == 2
    assert len(renderer.calls) == 2


@pytest.mark.anyio
async def test_rejected_measurement_seed_keeps_model_best_without_stagnation(
    tmp_path: Path,
) -> None:
    result, gateway, renderer, _memory_store = await run_scripted_graph(
        tmp_path,
        run_id="measurement-seed-rejected",
        gateway_script=[response(analysis_payload()), response(author_payload())],
        render_outcomes=[True, True],
        losses=[0.10, 0.20],
        policy=budget(visual=0, compile_repairs=0, model_calls=4),
        enable_measurement_seed=True,
    )

    assert result["current_best_id"] == "candidate-0001"
    assert result["current_best_record"].origin == "model"
    assert result["measurement_seed_attempted"] is True
    assert result["no_improvement_count"] == 0
    assert result["final_result"]["candidate_id"] == "candidate-0001"
    assert gateway.calls == 2
    assert len(renderer.calls) == 2


@pytest.mark.anyio
async def test_model_refinement_can_continue_from_accepted_measurement_seed(
    tmp_path: Path,
) -> None:
    refine = deepcopy(author_payload("visual_refine"))
    refine["base_candidate_id"] = "candidate-0002"
    result, gateway, renderer, _memory_store = await run_scripted_graph(
        tmp_path,
        run_id="measurement-seed-refined",
        gateway_script=[
            response(analysis_payload()),
            response(author_payload()),
            response(review_payload("candidate-0002")),
            response(refine),
        ],
        render_outcomes=[True, True, True],
        losses=[0.20, 0.10, 0.08],
        policy=budget(visual=1, compile_repairs=0, model_calls=6),
        enable_measurement_seed=True,
    )

    records = result["candidate_records"]
    assert [record.origin for record in records] == [
        "model",
        "deterministic",
        "model",
    ]
    assert records[2].parent_candidate_id == "candidate-0002"
    assert result["current_best_id"] == "candidate-0003"
    assert result["visual_refinement_count"] == 1
    assert result["model_call_count"] == 4
    assert gateway.calls == 4
    assert len(renderer.calls) == 3


@pytest.mark.anyio
async def test_failed_measurement_seed_keeps_best_without_model_compile_repair(
    tmp_path: Path,
) -> None:
    result, gateway, renderer, _memory_store = await run_scripted_graph(
        tmp_path,
        run_id="measurement-seed-compile-failed",
        gateway_script=[response(analysis_payload()), response(author_payload())],
        render_outcomes=[True, False],
        losses=[0.10],
        policy=budget(visual=0, compile_repairs=1, model_calls=5),
        enable_measurement_seed=True,
    )

    records = result["candidate_records"]
    assert result["current_best_id"] == "candidate-0001"
    assert records[1].origin == "deterministic"
    assert records[1].hard_constraints_passed is False
    assert records[1].compile_ref is not None
    assert result["compile_repair_count"] == 0
    assert result["no_improvement_count"] == 0
    assert result["final_result"]["candidate_id"] == "candidate-0001"
    assert gateway.calls == 2
    assert len(renderer.calls) == 2


@pytest.mark.anyio
async def test_first_candidate_success_finalizes_artifact_and_promotes_strategy(
    tmp_path: Path,
) -> None:
    result, gateway, renderer, memory_store = await run_scripted_graph(
        tmp_path,
        run_id="first-success",
        gateway_script=[response(analysis_payload()), response(author_payload())],
        render_outcomes=[True],
        losses=[0.30],
        policy=budget(visual=0, compile_repairs=0, model_calls=4),
    )

    final = result["final_result"]
    assert final["success"] is True
    assert final["candidate_id"] == "candidate-0001"
    assert final["glsl"] == GOLDEN_GLSL
    assert final["stop_reason"] == StopReason.VISUAL_ITERATION_BUDGET_EXHAUSTED.value
    assert final["candidate_count"] == 1
    assert result["memory_status"] == "ephemeral"
    assert gateway.calls == 2
    assert renderer.closed is True
    final_root = tmp_path / "artifacts/m3-tests/first-success/final"
    assert (final_root / "manifest.json").is_file()
    metrics = json.loads((final_root / "metrics.json").read_text())
    assert metrics["roi_losses"] == {"highlight": 0.3}
    assert metrics["protected_region_losses"] == {"protected_center": 0.1}
    assert metrics["effective_weights"] == {"global_rmse": 1.0}

    memories = await list_project_memories(memory_store, "m3-tests")
    assert len(memories) == 1
    assert memories[0].kind == "strategy"
    assert memories[0].glsl_sha256 == final["glsl_sha256"]


@pytest.mark.anyio
async def test_compile_failure_repairs_once_then_uses_repaired_candidate(
    tmp_path: Path,
) -> None:
    result, gateway, _renderer, _memory_store = await run_scripted_graph(
        tmp_path,
        run_id="compile-repair",
        gateway_script=[
            response(analysis_payload()),
            response(author_payload()),
            response(author_payload("compile_repair")),
        ],
        render_outcomes=[False, True],
        losses=[0.25],
        policy=budget(visual=0, compile_repairs=1, model_calls=5),
    )

    final = result["final_result"]
    assert final["success"] is True
    assert final["candidate_id"] == "candidate-0002"
    assert final["candidate_count"] == 2
    assert final["compile_repair_count"] == 1
    assert gateway.calls == 3
    assert result["candidate_records"][1].parent_candidate_id == "candidate-0001"
    compile_failure = next(
        event for event in result["events"] if event["event_type"] == "compile_failed"
    )
    compile_payload = compile_failure["payload"]
    assert "fragment_log" not in compile_payload
    assert "link_log" not in compile_payload
    assert compile_payload["fragment_log_chars"] > 0
    assert len(compile_payload["fragment_log_sha256"]) == 64


@pytest.mark.anyio
async def test_unvalidated_compile_failure_never_promotes_strategy(
    tmp_path: Path,
) -> None:
    result, _gateway, _renderer, memory_store = await run_scripted_graph(
        tmp_path,
        run_id="compile-exhausted",
        gateway_script=[response(analysis_payload()), response(author_payload())],
        render_outcomes=[False],
        losses=[],
        policy=budget(visual=0, compile_repairs=0, model_calls=4),
    )

    final = result["final_result"]
    assert final["success"] is False
    assert final["candidate_id"] is None
    assert final["stop_reason"] == StopReason.COMPILE_REPAIR_EXHAUSTED.value
    assert await list_project_memories(memory_store, "m3-tests") == ()
    assert result["events"][-1]["event_type"] == "strategy_promotion_skipped"


@pytest.mark.anyio
async def test_degraded_candidate_never_overwrites_current_best_or_final_glsl(
    tmp_path: Path,
) -> None:
    refine = deepcopy(author_payload("visual_refine"))
    refine["base_candidate_id"] = "candidate-0001"
    refine["glsl"] = GOLDEN_GLSL.replace("0.2, 0.5", "0.3, 0.5")
    result, _gateway, _renderer, _memory_store = await run_scripted_graph(
        tmp_path,
        run_id="keep-best",
        gateway_script=[
            response(analysis_payload()),
            response(author_payload()),
            response(review_payload("candidate-0001")),
            response(refine),
        ],
        render_outcomes=[True, True],
        losses=[0.20, 0.30],
        policy=budget(visual=1, compile_repairs=0, model_calls=6),
    )

    final = result["final_result"]
    assert result["glsl"] == refine["glsl"]
    assert final["glsl"] == GOLDEN_GLSL
    assert final["candidate_id"] == "candidate-0001"
    assert final["score_breakdown"]["total_loss"] == 0.20
    assert result["current_best_id"] == "candidate-0001"
    assert result["no_improvement_count"] == 1


@pytest.mark.anyio
async def test_two_non_improving_rounds_stop_with_stagnation_and_hard_bound(
    tmp_path: Path,
) -> None:
    refine = deepcopy(author_payload("visual_refine"))
    refine["base_candidate_id"] = "candidate-0001"
    script = [response(analysis_payload()), response(author_payload())]
    for _ in range(2):
        script.extend([response(review_payload("candidate-0001")), response(refine)])
    result, gateway, renderer, _memory_store = await run_scripted_graph(
        tmp_path,
        run_id="stagnation",
        gateway_script=script,
        render_outcomes=[True, True, True],
        losses=[0.20, 0.25, 0.24],
        policy=budget(visual=2, compile_repairs=0, model_calls=8),
    )

    final = result["final_result"]
    assert final["stop_reason"] == StopReason.STAGNATION.value
    assert final["candidate_id"] == "candidate-0001"
    assert final["candidate_count"] == 3
    assert final["visual_refinement_count"] == 2
    assert final["model_call_count"] == 6
    assert final["candidate_count"] <= 1 + 2 + 0
    assert gateway.calls == 6
    assert len(renderer.calls) == 3


@pytest.mark.anyio
async def test_model_failure_after_first_best_returns_existing_best(
    tmp_path: Path,
) -> None:
    result, gateway, _renderer, _memory_store = await run_scripted_graph(
        tmp_path,
        run_id="model-failure",
        gateway_script=[
            response(analysis_payload()),
            response(author_payload()),
            LLMInvocationError(
                "synthetic provider failure",
                model_ref="fake:quality",
                provider="fake",
                retryable=True,
            ),
        ],
        render_outcomes=[True],
        losses=[0.20],
        policy=budget(visual=2, compile_repairs=0, model_calls=8),
    )

    final = result["final_result"]
    assert final["success"] is True
    assert final["candidate_id"] == "candidate-0001"
    assert final["stop_reason"] == StopReason.COMPLETED_WITH_BEST_EFFORT.value
    assert final["model_call_count"] == 3
    assert gateway.calls == 3
    assert any(event["event_type"] == "model_failed" for event in result["events"])


@pytest.mark.anyio
async def test_graph_crosses_real_webgl_renderer_oracle_store_and_memory(
    tmp_path: Path,
) -> None:
    initial = author_payload()
    initial["glsl"] = GOLDEN_SHADER.read_text(encoding="utf-8")
    gateway = ScriptedGateway([response(analysis_payload()), response(initial)])
    memory_store = InMemoryStore()
    artifact_store = LocalArtifactStore(tmp_path / "real-artifacts")
    graph = build_png_to_shader_v1_graph(
        gateway,
        artifact_store=artifact_store,
        renderer_factory=lambda replay: PlaywrightWebGL1Renderer(
            replay_on_worker_failure=replay
        ),
        store=memory_store,
    )

    result = await graph.ainvoke(
        graph_input(
            "real-webgl",
            budget(visual=0, compile_repairs=0, model_calls=4),
        )
    )

    final = result["final_result"]
    assert final["success"] is True
    assert final["candidate_id"] == "candidate-0002"
    assert result["current_best_record"].origin == "deterministic"
    assert (
        result["current_best_record"].generator_version == "measurement_affine_seed_v1"
    )
    assert final["render_sha256"]
    assert 0.0 <= final["score_breakdown"]["total_loss"] < 0.5
    assert "highlight" in final["score_breakdown"]["roi_losses"]
    assert "subject" in final["score_breakdown"]["roi_losses"]
    assert (
        artifact_store.start_run("m3-tests", "real-webgl")
        .path_for(final["render_ref"])
        .is_file()
    )
    memories = await list_project_memories(memory_store, "m3-tests")
    assert [item.kind for item in memories] == ["strategy"]
