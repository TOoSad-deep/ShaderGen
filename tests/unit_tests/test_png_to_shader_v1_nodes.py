from __future__ import annotations

import asyncio
import json
import time
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent.app.config.model_config import NodeModelConfig
from agent.app.contracts.llm import LLMCallOptions, LLMResponse
from agent.app.messages.png_to_shader_v1 import InputBindingError
from agent.app.nodes import bounded_model_node as bounded_model_module
from agent.app.nodes.bounded_model_node import make_bounded_model_node
from agent.app.nodes.png_to_shader_v1 import (
    RunRendererRegistry,
    make_finalize_png_to_shader_v1_node,
    make_materialize_candidate_node,
    make_persist_visual_review_node,
    make_prepare_measurement_seed_node,
    make_render_and_evaluate_node,
)
from agent.app.nodes.png_to_shader_v1 import finalization as finalization_module
from agent.app.nodes.png_to_shader_v1 import runtime as run_nodes_runtime
from agent.app.nodes.shader_author_node import (
    make_shader_author_compile_repair_node,
    make_shader_author_initial_node,
    make_shader_author_visual_refine_node,
)
from agent.app.nodes.structured_output import (
    StructuredOutputExhaustedError,
    StructuredOutputInvocationError,
)
from agent.app.nodes.visual_analysis_node import make_visual_analysis_node
from agent.app.nodes.visual_critic_node import make_visual_critic_node
from shaderforge.analysis import measure_target, normalize_target_png
from shaderforge.contracts import BudgetPolicy, StopReason
from shaderforge.evaluation import CandidateRecord, ScoreBreakdownV1
from shaderforge.rendering import CompileResult, RenderResult
from shaderforge.store import LocalArtifactStore
from shaderforge.validation import validate_shader
from tests.unit_tests.png_to_shader_v1_samples import (
    GOLDEN_GLSL,
    analysis_payload,
    author_payload,
    json_text,
    review_payload,
)

ROOT = Path(__file__).resolve().parents[2]
REFERENCE_IMAGE = ROOT / "benchmarks/png_to_shader_v1/images/pink_gel.png"


class FakeGateway:
    def __init__(self, responses: list[LLMResponse | Exception]) -> None:
        self._responses = list(responses)
        self.calls = []

    async def ainvoke(self, messages, options):
        self.calls.append((messages, options))
        if not self._responses:
            raise AssertionError("发生了未预期的额外模型调用")
        value = self._responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def response(
    text: str,
    *,
    actual_model: str = "openai:gpt-quality-actual",
    requested_model: str = "openai:gpt-quality-requested",
) -> LLMResponse:
    return LLMResponse(
        message=AIMessage(content=text),
        text=text,
        reasoning_content=None,
        model_ref=actual_model,
        requested_model_ref=requested_model,
        model_identity_source="response_metadata",
        latency_ms=9,
    )


def quality_config() -> NodeModelConfig:
    return NodeModelConfig(
        call=LLMCallOptions(
            model_ref="openai:gpt-quality-requested",
            temperature=0,
            thinking="on",
            capture_reasoning=True,
        ),
        print_reasoning=False,
    )


@pytest.mark.anyio
async def test_renderer_registry_retries_close_after_timeout() -> None:
    class TimeoutOnceRenderer:
        def __init__(self) -> None:
            self.close_calls = 0
            self.closed = False

        async def render(self, _fragment_source: str, _width: int, _height: int):
            return object()

        async def close(self) -> None:
            self.close_calls += 1
            if self.close_calls == 1:
                await asyncio.Event().wait()
            self.closed = True

    renderer = TimeoutOnceRenderer()
    registry = RunRendererRegistry(lambda _replay: renderer)  # type: ignore[arg-type]
    key = ("project-close-retry", "run-close-retry")
    await registry.render(
        key,
        replay_on_worker_failure=0,
        fragment_source=GOLDEN_GLSL,
        width=1,
        height=1,
    )

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(registry.close(key), timeout=0.01)
    await registry.close(key)

    assert renderer.close_calls == 2
    assert renderer.closed is True


def test_evaluation_measurements_merge_visual_analysis_semantic_regions() -> None:
    measurements = measure_target(REFERENCE_IMAGE.read_bytes())
    analysis = analysis_payload()

    merged = run_nodes_runtime._evaluation_measurements(
        {"visual_analysis": analysis},
        measurements,
    )

    region_ids = [region.region_id for region in merged.roi_candidates]
    assert region_ids.count("subject") == 1
    assert "highlight" in region_ids
    semantic = next(
        region for region in merged.roi_candidates if region.region_id == "highlight"
    )
    assert semantic.purpose == "highlight"
    assert semantic.bbox_uv == (0.2, 0.65, 0.5, 0.88)


@pytest.mark.anyio
async def test_visual_reviews_keep_iteration_specific_artifacts(tmp_path: Path) -> None:
    artifacts = LocalArtifactStore(tmp_path / "review-artifacts")
    store = artifacts.register_run("project-review", "run-review")
    glsl_ref = store.write_text("candidate.frag", GOLDEN_GLSL)
    author_ref = store.write_json("author.json", author_payload())
    provenance_ref = store.write_json("provenance.json", {"source": "unit"})
    best = CandidateRecord(
        candidate_id="candidate-0001",
        parent_candidate_id=None,
        glsl_sha256=glsl_ref.sha256,
        glsl_ref=glsl_ref.relative_path,
        author_ref=author_ref.relative_path,
        provenance_ref=provenance_ref.relative_path,
        compile_ref="compile.json",
        render_ref="render.png",
        render_sha256="1" * 64,
        metrics_ref="metrics.json",
        review_ref=None,
        iteration=0,
        changed_problem_domain="initial_build",
        prompt_version="shader_author_initial_v1_1",
        model_ref="fake:model",
        score_summary=ScoreBreakdownV1(
            metric_version="unit_test_v1",
            total_loss=0.2,
            global_rmse=0.2,
            global_mae=0.2,
            edge_loss=0.2,
            geometry_loss=0.2,
            representative_pixel_loss=0.2,
            roi_losses=(),
            protected_region_losses=(),
            effective_weights=(("global_rmse", 1.0),),
            diagnostics=(),
        ),
        hard_constraints_passed=True,
    )
    node = make_persist_visual_review_node(artifacts)
    base_state = {
        "project_id": "project-review",
        "run_id": "run-review",
        "current_best_record": best,
        "candidate_records": (best,),
        "visual_review": review_payload("candidate-0001"),
        "events": (),
    }

    first = await node({**base_state, "visual_refinement_count": 0})
    second = await node({**base_state, "visual_refinement_count": 1})

    first_ref = first["current_best_record"].review_ref
    second_ref = second["current_best_record"].review_ref
    assert first_ref == "candidates/candidate-0001/reviews/review-0001.json"
    assert second_ref == "candidates/candidate-0001/reviews/review-0002.json"
    assert first_ref != second_ref
    assert store.read_bytes(first_ref)
    assert store.read_bytes(second_ref)


@pytest.mark.anyio
async def test_prepare_measurement_seed_has_deterministic_provenance() -> None:
    reference = normalize_target_png(REFERENCE_IMAGE.read_bytes())
    node = make_prepare_measurement_seed_node()

    result = await node(
        {
            "image": reference,
            "target_measurements": measure_target(reference),
            "measurement_seed_attempted": False,
            "events": (),
        }
    )

    assert result["measurement_seed_attempted"] is True
    assert result["candidate_origin"] == "deterministic"
    assert result["candidate_generator_version"] == "measurement_affine_seed_v1"
    assert result["candidate_provenance"]["origin"] == "deterministic"
    assert result["candidate_provenance"]["generator_version"] == (
        "measurement_affine_seed_v1"
    )
    assert result["author_result"]["mode"] == "measurement_seed"
    assert "texture2D" not in result["glsl"]

    with pytest.raises(RuntimeError, match="只能准备一次"):
        await node(
            {
                **result,
                "image": reference,
                "target_measurements": measure_target(reference),
            }
        )


def analysis_state() -> dict:
    return {
        "image": b"reference",
        "content_type": "image/png",
        "target_measurements": {
            "image_sha256": sha256(b"reference").hexdigest(),
            "foreground_bbox_uv": [0.15, 0.15, 0.85, 0.85],
        },
        "instruction": "复刻粉色凝胶球",
        "model_calls": ({"existing": True},),
    }


def bound_candidate_state() -> dict:
    rendered = b"rendered-png"
    glsl_hash = sha256(GOLDEN_GLSL.encode()).hexdigest()
    render_hash = sha256(rendered).hexdigest()
    candidate = {
        "candidate_id": "candidate-best",
        "parent_candidate_id": None,
        "glsl_sha256": glsl_hash,
        "render_sha256": render_hash,
        "prompt_version": "shader_author_initial_v1",
        "model_ref": "openai:gpt-quality-actual",
        "iteration": 0,
    }
    return {
        "image": b"reference",
        "content_type": "image/png",
        "rendered_image": rendered,
        "rendered_content_type": "image/png",
        "glsl": GOLDEN_GLSL,
        "target_measurements": {"foreground_bbox_uv": [0.15, 0.15, 0.85, 0.85]},
        "visual_analysis": analysis_payload(),
        "score_breakdown": {"total_loss": 0.2, "roi_losses": {"highlight": 0.3}},
        "residual_summary": {"highlight": "too long"},
        "current_candidate": candidate,
        "current_best_candidate": candidate,
        "render_evidence_binding": {
            "candidate_id": "candidate-best",
            "glsl_sha256": glsl_hash,
            "image_sha256": render_hash,
        },
        "visual_review": review_payload(),
    }


def glsl_from_message_part(part: dict) -> str:
    text = part["text"]
    payload = text.split(">", 1)[1].rsplit("</", 1)[0]
    return json.loads(payload)["glsl"]


@pytest.mark.anyio
async def test_analysis_node_uses_system_prompt_actual_model_and_does_not_mutate() -> (
    None
):
    gateway = FakeGateway([response(json_text(analysis_payload()))])
    node = make_visual_analysis_node(gateway, quality_config())
    state = analysis_state()
    original = deepcopy(state)

    result = await node(state)

    assert state == original
    assert result["visual_analysis"]["analysis_version"] == "visual_analysis_v1_2"
    assert result["visual_analysis_model"] == "openai:gpt-quality-actual"
    assert result["model_calls"][0] == {"existing": True}
    assert result["model_calls"][1]["model_ref"] == "openai:gpt-quality-actual"
    assert (
        result["model_calls"][1]["requested_model_ref"]
        == "openai:gpt-quality-requested"
    )
    messages, options = gateway.calls[0]
    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], HumanMessage)
    assert options.thinking == "on"


@pytest.mark.anyio
async def test_single_json_fence_is_parsed_locally_without_repair_call() -> None:
    text = json_text(analysis_payload())
    gateway = FakeGateway([response(f"```json\n{text}\n```")])

    result = await make_visual_analysis_node(gateway, quality_config())(
        analysis_state()
    )

    assert result["visual_analysis"]["summary"]
    assert len(gateway.calls) == 1


@pytest.mark.anyio
async def test_known_roi_purpose_alias_is_repaired_locally_and_audited() -> None:
    payload = analysis_payload()
    payload["regions_of_interest"][0]["purpose"] = "background"
    gateway = FakeGateway([response(json_text(payload))])

    result = await make_visual_analysis_node(gateway, quality_config())(
        {**analysis_state(), "run_id": "run-1", "project_id": "project-1"}
    )

    assert len(gateway.calls) == 1
    assert result["visual_analysis"]["regions_of_interest"][0]["purpose"] == (
        "protection"
    )
    assert result["model_calls"][-1]["parse_status"] == "invalid"
    assert result["logs"][-1]["context"] == {
        "strategy": "visual_analysis_roi_purpose_alias_v1",
        "repaired_paths": ["$.'regions_of_interest'.'0'.'purpose'"],
        "source_error_codes": ["invalid_literal"],
    }


@pytest.mark.anyio
async def test_invalid_json_gets_exactly_one_low_reasoning_repair() -> None:
    gateway = FakeGateway(
        [
            response("not-json", actual_model="openai:semantic-actual"),
            response(
                json_text(analysis_payload()), actual_model="openai:repair-actual"
            ),
        ]
    )

    result = await make_visual_analysis_node(gateway, quality_config())(
        analysis_state()
    )

    assert len(gateway.calls) == 2
    repair_messages, repair_options = gateway.calls[1]
    assert repair_options.model_ref == "openai:gpt-quality-requested"
    assert repair_options.thinking == "off"
    assert repair_options.capture_reasoning is False
    assert isinstance(repair_messages[0], SystemMessage)
    assert "结构化输出修复器" in repair_messages[0].content
    assert result["model_calls"][-2]["parse_status"] == "invalid"
    assert result["model_calls"][-2]["response_format"] == "text"
    assert result["model_calls"][-1]["parse_status"] == "valid"
    assert result["model_calls"][-1]["model_ref"] == "openai:repair-actual"
    assert (
        result["model_calls"][-1]["repair_prompt_version"]
        == "structured_output_repair_v1_2"
    )


@pytest.mark.anyio
async def test_missing_field_uses_same_single_repair_path() -> None:
    missing = analysis_payload()
    missing.pop("layers")
    gateway = FakeGateway(
        [response(json_text(missing)), response(json_text(analysis_payload()))]
    )

    result = await make_visual_analysis_node(gateway, quality_config())(
        analysis_state()
    )

    assert len(gateway.calls) == 2
    assert "missing_field" in result["model_calls"][-2]["error_codes"]
    assert result["model_calls"][-2]["validation_issues"] == [
        {
            "code": "missing_field",
            "message": "Field required",
            "path": "$.'layers'",
        }
    ]


@pytest.mark.anyio
async def test_second_invalid_output_raises_safe_explicit_error_without_third_call() -> (
    None
):
    gateway = FakeGateway([response("secret-first"), response("secret-second")])

    with pytest.raises(StructuredOutputExhaustedError) as caught:
        await make_visual_analysis_node(gateway, quality_config())(analysis_state())

    assert len(gateway.calls) == 2
    assert len(caught.value.audits) == 2
    assert "secret-first" not in str(caught.value)
    assert "secret-second" not in str(caught.value)


@pytest.mark.anyio
async def test_structured_repair_is_not_attempted_when_only_one_model_call_remains() -> (
    None
):
    gateway = FakeGateway([response("invalid-json")])
    state = {**analysis_state(), "structured_output_max_attempts": 1}

    with pytest.raises(StructuredOutputExhaustedError) as caught:
        await make_visual_analysis_node(gateway, quality_config())(state)

    assert len(gateway.calls) == 1
    assert len(caught.value.audits) == 1


@pytest.mark.anyio
async def test_repair_provider_failure_records_both_attempts_without_error_text() -> (
    None
):
    gateway = FakeGateway(
        [response("invalid-json"), RuntimeError("secret provider detail")]
    )

    with pytest.raises(StructuredOutputInvocationError) as caught:
        await make_visual_analysis_node(gateway, quality_config())(analysis_state())

    assert len(gateway.calls) == 2
    assert caught.value.attempted_calls == 2
    assert len(caught.value.audits) == 1
    assert caught.value.error_type == "RuntimeError"
    assert "secret provider detail" not in str(caught.value)


@pytest.mark.anyio
async def test_initial_author_returns_full_glsl_and_candidate_provenance() -> None:
    gateway = FakeGateway([response(json_text(author_payload()))])
    state = {
        **analysis_state(),
        "visual_analysis": analysis_payload(),
    }

    result = await make_shader_author_initial_node(gateway, quality_config())(state)

    assert result["glsl"] == GOLDEN_GLSL
    assert result["author_result"]["mode"] == "initial"
    assert result["candidate_provenance"]["mode"] == "initial"
    assert result["candidate_provenance"]["model_ref"] == "openai:gpt-quality-actual"
    assert (
        result["candidate_provenance"]["prompt_version"] == "shader_author_initial_v1_1"
    )
    assert result["candidate_provenance"]["final_attempt"] == 1
    assert (
        result["candidate_provenance"]["glsl_sha256"]
        == sha256(GOLDEN_GLSL.encode()).hexdigest()
    )


@pytest.mark.anyio
async def test_initial_author_normalizes_only_fixed_bindings_without_second_call() -> (
    None
):
    payload = author_payload()
    payload["changed_parameters"] = ["radius"]
    payload["protected_regions"] = ["subject"]
    gateway = FakeGateway([response(json_text(payload))])
    state = {
        **analysis_state(),
        "visual_analysis": analysis_payload(),
        "run_id": "run-local-author-repair",
        "project_id": "project-local-author-repair",
    }

    result = await make_shader_author_initial_node(gateway, quality_config())(state)

    assert len(gateway.calls) == 1
    assert result["author_result"]["mode"] == "initial"
    assert result["author_result"]["base_candidate_id"] is None
    assert result["author_result"]["changed_parameters"] == []
    assert result["author_result"]["protected_regions"] == []
    assert result["model_calls"][-1]["parse_status"] == "invalid"
    assert result["logs"][-1]["context"]["strategy"] == (
        "shader_author_initial_fixed_bindings_v1"
    )


@pytest.mark.anyio
async def test_compile_author_preserves_scope_and_puts_current_glsl_last() -> None:
    gateway = FakeGateway([response(json_text(author_payload("compile_repair")))])
    state = {
        "previous_author_result": author_payload(),
        "glsl": GOLDEN_GLSL,
        "static_validation": {"valid": False, "violations": ["missing semicolon"]},
        "compile_result": {"success": False, "fragment_log": "missing semicolon"},
        "repair_budget": {"remaining": 1},
    }

    result = await make_shader_author_compile_repair_node(gateway, quality_config())(
        state
    )

    assert result["author_result"]["changed_problem_domain"] == "runtime_compile"
    messages, _ = gateway.calls[0]
    assert "current_glsl" in messages[1].content[-1]["text"]
    assert glsl_from_message_part(messages[1].content[-1]) == GOLDEN_GLSL


@pytest.mark.anyio
async def test_critic_rejects_hash_mismatch_before_gateway_call() -> None:
    state = bound_candidate_state()
    state["render_evidence_binding"]["image_sha256"] = "0" * 64
    gateway = FakeGateway([])

    with pytest.raises(InputBindingError):
        await make_visual_critic_node(gateway, quality_config())(state)

    assert gateway.calls == []


@pytest.mark.anyio
async def test_critic_returns_review_and_places_current_glsl_last() -> None:
    state = bound_candidate_state()
    gateway = FakeGateway([response(json_text(review_payload()))])

    result = await make_visual_critic_node(gateway, quality_config())(state)

    assert result["visual_review"]["primary_problem_domain"] == "highlight"
    messages, _ = gateway.calls[0]
    assert isinstance(messages[0], SystemMessage)
    assert "current_glsl" in messages[1].content[-1]["text"]
    assert glsl_from_message_part(messages[1].content[-1]) == GOLDEN_GLSL


@pytest.mark.anyio
async def test_visual_refine_binds_current_best_and_protected_regions() -> None:
    state = bound_candidate_state()
    gateway = FakeGateway([response(json_text(author_payload("visual_refine")))])

    result = await make_shader_author_visual_refine_node(gateway, quality_config())(
        state
    )

    assert result["author_result"]["base_candidate_id"] == "candidate-best"
    assert result["author_result"]["protected_regions"] == ["subject"]
    messages, _ = gateway.calls[0]
    assert "current_best_glsl" in messages[1].content[-1]["text"]


def test_role_default_configs_prioritize_reliable_structured_output() -> None:
    from agent.app.nodes.shader_author_node import SHADER_AUTHOR_MODEL_CONFIG
    from agent.app.nodes.visual_analysis_node import VISUAL_ANALYSIS_MODEL_CONFIG
    from agent.app.nodes.visual_critic_node import VISUAL_CRITIC_MODEL_CONFIG

    for config in (
        VISUAL_ANALYSIS_MODEL_CONFIG,
        SHADER_AUTHOR_MODEL_CONFIG,
        VISUAL_CRITIC_MODEL_CONFIG,
    ):
        assert config.call.thinking == "off"
        assert config.call.capture_reasoning is False
        assert config.call.response_format == "json_object"
        assert config.call.temperature == 0


@pytest.mark.anyio
async def test_render_node_repairs_constant_reversed_smoothstep_before_renderer(
    tmp_path: Path,
) -> None:
    original_glsl = GOLDEN_GLSL.replace(
        "smoothstep(radius, radius + 0.01,",
        "smoothstep(0.36, 0.34,",
    )
    author = author_payload()
    author["glsl"] = original_glsl
    original_sha256 = sha256(original_glsl.encode()).hexdigest()
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    artifacts.register_run("project-repair", "run-repair")
    state = {
        "project_id": "project-repair",
        "run_id": "run-repair",
        "glsl": original_glsl,
        "author_result": author,
        "candidate_provenance": {
            "mode": "initial",
            "model_ref": "fake:model",
            "requested_model_ref": "fake:model",
            "model_identity_source": "configured_fallback",
            "prompt_version": "shader_author_initial_v1_1",
            "final_attempt": 1,
            "repair_prompt_version": None,
            "output_sha256": "0" * 64,
            "glsl_sha256": original_sha256,
        },
        "candidate_sequence": 0,
        "candidate_records": (),
        "visual_refinement_count": 0,
        "events": (),
    }
    state.update(await make_materialize_candidate_node(artifacts)(state))

    class CapturingRendererRegistry:
        source = ""

        async def render(self, _key, **kwargs):
            self.source = str(kwargs["fragment_source"])
            validation = validate_shader(self.source)
            return RenderResult(
                success=True,
                image_bytes=b"synthetic-png",
                width=int(kwargs["width"]),
                height=int(kwargs["height"]),
                compile=CompileResult(
                    success=True,
                    vertex_log="",
                    fragment_log="",
                    link_log="",
                    draw_error=None,
                    static_validation=validation,
                ),
                console_errors=(),
                metadata=None,
                duration_ms=1.0,
            )

    def evaluator(_reference, _candidate, *, measurements):
        assert measurements.analysis_width > 0
        return ScoreBreakdownV1(
            metric_version="unit_test_v1",
            total_loss=0.2,
            global_rmse=0.2,
            global_mae=0.2,
            edge_loss=0.2,
            geometry_loss=0.2,
            representative_pixel_loss=0.2,
            roi_losses=(),
            protected_region_losses=(),
            effective_weights=(("global_rmse", 1.0),),
            diagnostics=(),
        )

    image = REFERENCE_IMAGE.read_bytes()
    state.update(
        {
            "image": image,
            "target_measurements": measure_target(image),
            "budget_policy": asdict(
                BudgetPolicy(
                    max_visual_refinements=0,
                    max_compile_repairs=1,
                    max_model_calls=4,
                    max_wall_time_seconds=300,
                )
            ),
            "started_at": 0.0,
        }
    )
    renderer = CapturingRendererRegistry()
    node = make_render_and_evaluate_node(
        artifacts,
        renderer,  # type: ignore[arg-type]
        evaluator,
        clock=lambda: 1.0,
    )

    result = await node(state)

    assert result["render_status"] == "success"
    assert validate_shader(renderer.source).valid
    assert "(1.0 - smoothstep(0.34, 0.36," in renderer.source
    assert result["glsl"] == renderer.source
    assert (
        result["candidate_record"].glsl_sha256
        == sha256(renderer.source.encode()).hexdigest()
    )
    repair_event = next(
        event
        for event in result["events"]
        if event["event_type"] == "shader_deterministically_repaired"
    )
    assert repair_event["payload"]["before_glsl_sha256"] == original_sha256
    assert repair_event["payload"]["replacement_count"] == 1
    assert "precision mediump" not in json.dumps(repair_event)
    assert "precision mediump" not in json.dumps(result["logs"][-1])


def test_validation_event_diagnostics_include_codes_and_source_lines() -> None:
    shader = GOLDEN_GLSL.replace(
        "smoothstep(radius, radius + 0.01,",
        "smoothstep(0.3, 0.3,",
    )

    diagnostics = run_nodes_runtime._validation_diagnostics(validate_shader(shader))

    assert diagnostics["violation_codes"] == ["reversed_smoothstep_edges"]
    assert diagnostics["violations"] == [
        {
            "code": "reversed_smoothstep_edges",
            "severity": "error",
            "line": 9,
        }
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("failure_kind", ["error", "timeout"])
async def test_evaluation_failure_returns_unscored_validated_fallback_and_close_is_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    artifacts = LocalArtifactStore(tmp_path / "fallback-artifacts")
    artifacts.register_run("project-fallback", "run-fallback")
    author = author_payload()
    glsl_sha256 = sha256(GOLDEN_GLSL.encode()).hexdigest()
    state = {
        "project_id": "project-fallback",
        "run_id": "run-fallback",
        "glsl": GOLDEN_GLSL,
        "author_result": author,
        "candidate_provenance": {
            "mode": "initial",
            "model_ref": "fake:model",
            "requested_model_ref": "fake:model",
            "model_identity_source": "configured_fallback",
            "prompt_version": "shader_author_initial_v1_1",
            "final_attempt": 1,
            "repair_prompt_version": None,
            "output_sha256": "0" * 64,
            "glsl_sha256": glsl_sha256,
        },
        "candidate_sequence": 0,
        "candidate_records": (),
        "visual_refinement_count": 0,
        "compile_repair_count": 0,
        "model_call_count": 2,
        "no_improvement_count": 0,
        "events": (),
        "logs": (),
    }
    state.update(await make_materialize_candidate_node(artifacts)(state))

    class RendererRegistry:
        async def render(self, _key, **kwargs):
            validation = validate_shader(str(kwargs["fragment_source"]))
            return RenderResult(
                success=True,
                image_bytes=b"validated-render",
                width=int(kwargs["width"]),
                height=int(kwargs["height"]),
                compile=CompileResult(
                    success=True,
                    vertex_log="",
                    fragment_log="",
                    link_log="",
                    draw_error=None,
                    static_validation=validation,
                ),
                console_errors=(),
                metadata=None,
                duration_ms=1.0,
            )

        async def close(self, _key):
            await asyncio.Event().wait()

    def failing_evaluator(_reference, _candidate, *, measurements):
        assert measurements.analysis_height > 0
        if failure_kind == "error":
            raise RuntimeError("synthetic evaluator failure")
        time.sleep(0.05)
        return ScoreBreakdownV1(
            metric_version="too_late_v1",
            total_loss=0.2,
            global_rmse=0.2,
            global_mae=0.2,
            edge_loss=0.2,
            geometry_loss=0.2,
            representative_pixel_loss=0.2,
            roi_losses=(),
            protected_region_losses=(),
            effective_weights=(("global_rmse", 1.0),),
            diagnostics=(),
        )

    image = REFERENCE_IMAGE.read_bytes()
    measurements = measure_target(image)
    state.update(
        {
            "image": image,
            "target_measurements": measurements,
            "budget_policy": asdict(
                BudgetPolicy(
                    max_visual_refinements=0,
                    max_compile_repairs=1,
                    max_model_calls=4,
                    max_wall_time_seconds=1 if failure_kind == "timeout" else 300,
                )
            ),
            "started_at": 0.0,
        }
    )
    registry = RendererRegistry()
    if failure_kind == "timeout":
        clock_values = iter((0.0, 0.899, 0.899, 0.901))

        def clock() -> float:
            return next(clock_values, 0.901)

    else:

        def clock() -> float:
            return 1.0

    render_node = make_render_and_evaluate_node(
        artifacts,
        registry,  # type: ignore[arg-type]
        failing_evaluator,
        clock=clock,
    )

    render_update = await render_node(state)

    assert render_update["render_status"] == "evaluation_failed"
    assert render_update["candidate_record"].hard_constraints_passed is True
    assert render_update["candidate_record"].score_summary is None
    evaluation_event = next(
        event
        for event in render_update["events"]
        if event["event_type"] == "evaluation_failed"
    )
    assert evaluation_event["payload"]["error_type"] == (
        "TimeoutError" if failure_kind == "timeout" else "RuntimeError"
    )
    merged = {**state, **render_update}
    monkeypatch.setattr(
        finalization_module,
        "RENDERER_CLOSE_TIMEOUT_SECONDS",
        0.01,
    )
    finalize = make_finalize_png_to_shader_v1_node(
        artifacts,
        registry,  # type: ignore[arg-type]
        clock=clock,
    )

    final_update = await finalize(merged)

    final = final_update["final_result"]
    assert final["success"] is True
    assert final["candidate_id"] == "candidate-0001"
    assert final["stop_reason"] == StopReason.COMPLETED_WITH_BEST_EFFORT.value
    assert final["score_breakdown"] is None
    assert final["metrics_ref"] is None
    assert final["unscored_fallback"] is True
    assert any(
        event["event_type"] == "validated_candidate_fallback_selected"
        for event in final_update["events"]
    )
    assert any(
        event["event_type"] == "renderer_close_failed"
        and event["payload"]["error_type"] == "TimeoutError"
        for event in final_update["events"]
    )


def test_balanced_render_work_window_keeps_thirty_seconds_for_finalize() -> None:
    state = {
        "budget_policy": asdict(
            BudgetPolicy(
                max_visual_refinements=2,
                max_compile_repairs=2,
                max_model_calls=8,
                max_wall_time_seconds=300,
            )
        ),
        "started_at": 0.0,
    }

    assert run_nodes_runtime._finalize_reserve_seconds(state) == 30.0
    assert run_nodes_runtime._work_seconds_before_finalize(state, lambda: 100.0) == (
        170.0
    )


@pytest.mark.anyio
async def test_bounded_model_stage_cap_preserves_global_time_and_reports_after_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        bounded_model_module.STAGE_TIMEOUT_CAP_SECONDS,
        "author_compile_repair",
        0.01,
    )

    async def never_finishes(_state):
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    started_at = time.monotonic()
    state = {
        "project_id": "project-timeout",
        "run_id": "run-timeout",
        "budget_policy": asdict(
            BudgetPolicy(
                max_visual_refinements=2,
                max_compile_repairs=2,
                max_model_calls=8,
                max_wall_time_seconds=300,
            )
        ),
        "started_at": started_at,
        "model_call_count": 2,
        "events": (),
        "current_best_record": {"candidate_id": "validated-best"},
    }
    node = make_bounded_model_node(
        never_finishes,
        stage="author_compile_repair",
        clock=time.monotonic,
    )

    result = await node(state)

    assert result["stop_reason"] == StopReason.COMPLETED_WITH_BEST_EFFORT.value
    assert state["current_best_record"] == {"candidate_id": "validated-best"}
    event = result["events"][-1]
    assert event["payload"]["timeout_source"] == "stage_cap"
    assert event["payload"]["timeout_seconds"] == 0.01
    assert event["payload"]["stage_elapsed_seconds"] >= 0.005
    assert 0.0 < event["payload"]["remaining_wall_seconds"] < 300.0


@pytest.mark.anyio
async def test_bounded_model_does_not_call_delegate_inside_downstream_reserve() -> None:
    calls = 0

    async def delegate(_state):
        nonlocal calls
        calls += 1
        return {}

    state = {
        "project_id": "project-reserve",
        "run_id": "run-reserve",
        "budget_policy": asdict(
            BudgetPolicy(
                max_visual_refinements=1,
                max_compile_repairs=1,
                max_model_calls=5,
                max_wall_time_seconds=30,
            )
        ),
        "started_at": 0.0,
        "model_call_count": 1,
        "events": (),
    }
    node = make_bounded_model_node(
        delegate,
        stage="visual_critic",
        clock=lambda: 28.0,
    )

    result = await node(state)

    assert calls == 0
    assert result["stop_reason"] == StopReason.WALL_TIME_EXHAUSTED.value
    event = result["events"][-1]
    assert event["payload"]["attempted_calls"] == 0
    assert event["payload"]["timeout_source"] == "downstream_reserve"
    assert event["payload"]["remaining_wall_seconds"] == 2.0
    assert event["payload"]["reserved_wall_seconds"] == 3.0


@pytest.mark.anyio
async def test_bounded_model_propagates_unexpected_internal_error() -> None:
    async def broken_delegate(_state):
        raise AssertionError("synthetic invariant failure")

    state = {
        "project_id": "project-internal-error",
        "run_id": "run-internal-error",
        "budget_policy": asdict(
            BudgetPolicy(
                max_visual_refinements=1,
                max_compile_repairs=1,
                max_model_calls=5,
                max_wall_time_seconds=300,
            )
        ),
        "started_at": 0.0,
        "model_call_count": 0,
        "events": (),
    }
    node = make_bounded_model_node(
        broken_delegate,
        stage="author_initial",
        clock=lambda: 1.0,
    )

    with pytest.raises(AssertionError, match="synthetic invariant failure"):
        await node(state)
