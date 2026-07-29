"""LayerPlan/direct GLSL 单 engine runner 的隔离与失败收敛测试."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from agent.app.nodes.layered_direct.authors import (
    ValidatedLayeredIncumbent,
    _refine_context_sha256,
    run_refine_layered_glsl_author,
)
from agent.app.services.layerplan_glsl_direct import (
    DIRECT_ENGINE_ID,
    DIRECT_REPRESENTATION,
    DirectAttemptResult,
    DirectOptimizationPolicy,
    LayerPlanGlslDirectConfig,
    LayerPlanGlslDirectRunner,
    current_layered_direct_glsl_implementation_identity,
)
from shaderforge.uniform_optimization import UniformOptimizationSummaryV2
from tests.direct_fakes import (
    CANVAS,
)
from tests.direct_fakes import (
    TEST_ISSUER as _TEST_ISSUER,
)
from tests.direct_fakes import (
    FakeGateway as _FakeGateway,
)
from tests.direct_fakes import (
    FakeRenderer as _FakeRenderer,
)
from tests.direct_fakes import (
    reference_png as _reference_png,
)

IMPLEMENTATION_SHA256 = "a" * 64


def _layered_payload(gain: float) -> str:
    return json.dumps(
        {
            "schema_version": "layered_shader_spec_v1",
            "canvas": {"width": CANVAS, "height": CANVAS},
            "layers": [
                {
                    "layer_id": "bg",
                    "role": "background",
                    "z_index": 0,
                    "glsl_body": "return vec4(vec3(u_gain), 1.0);",
                    "uniform_schema": {
                        "u_gain": {
                            "type": "float",
                            "minimum": 0.0,
                            "maximum": 1.0,
                            "default": 0.5,
                        }
                    },
                    "uniform_values": {"u_gain": gain},
                    "tunable_manifest": [
                        {
                            "path": "u_gain",
                            "type": "float",
                            "minimum": 0.0,
                            "maximum": 1.0,
                            "step": 0.01,
                        }
                    ],
                }
            ],
        }
    )


def _layered_payload_above_program_spec_uniform_defaults() -> str:
    payload = json.loads(_layered_payload(0.5))
    layer = payload["layers"][0]
    for index in range(17):
        name = f"u_extra_{index}"
        layer["uniform_schema"][name] = {
            "type": "vec4",
            "minimum": [0.0, 0.0, 0.0, 0.0],
            "maximum": [1.0, 1.0, 1.0, 1.0],
            "default": [0.5, 0.5, 0.5, 0.5],
        }
        layer["uniform_values"][name] = [0.5, 0.5, 0.5, 0.5]
    return json.dumps(payload)


def _tagged_json(messages: Any, label: str) -> dict[str, Any]:
    opening = f"<{label}>"
    closing = f"</{label}>"
    for part in messages[1].content:
        if not isinstance(part, dict):
            continue
        text = str(part.get("text", ""))
        if opening not in text:
            continue
        payload = text.split(opening, 1)[1].split(closing, 1)[0]
        value = json.loads(payload)
        assert isinstance(value, dict)
        return value
    raise AssertionError(f"missing tagged JSON: {label}")


class _LayeredFakeGateway(_FakeGateway):
    """复用当前 fake 调用身份，并为 Refine 动态回显可信 Patch guard."""

    def __init__(
        self,
        *,
        initial_gains: tuple[float, ...] = (0.5,),
        refine_gains: tuple[float, ...] = (0.5,),
    ) -> None:
        super().__init__(
            initial_responses=[_layered_payload(value) for value in initial_gains],
            refine_responses=["unused"],
        )
        self._refine_gains = list(refine_gains)

    async def ainvoke(self, messages: Any, options: Any) -> Any:
        system_text = str(messages[0].content)
        if "direct layered GLSL Refine Author" in system_text:
            incumbent = _tagged_json(messages, "current_best_layered_spec")
            layers = incumbent["layers"]
            assert isinstance(layers, list) and layers
            target = layers[0]
            assert isinstance(target, dict)
            gain = (
                self._refine_gains.pop(0)
                if len(self._refine_gains) > 1
                else self._refine_gains[0]
            )
            patch = {
                "schema_version": "layer_patch_v1",
                "base_layered_spec_sha256": incumbent["layered_spec_sha256"],
                "target_layer_id": target["layer_id"],
                "expected_layer_sha256": target["layer_sha256"],
                "replacement": json.loads(_layered_payload(gain))["layers"][0],
            }
            self._queues[("refine", False)] = [json.dumps(patch)]
        return await super().ainvoke(messages, options)


class _LayeredRepairGateway(_FakeGateway):
    async def ainvoke(self, messages: Any, options: Any) -> Any:
        if "只修复 ShaderGen direct layered 作者" in str(messages[0].content):
            self._last_text = None
        return await super().ainvoke(messages, options)


async def _run(
    gateway: Any,
    renderer: Any,
    config: LayerPlanGlslDirectConfig | None = None,
) -> DirectAttemptResult:
    runner = LayerPlanGlslDirectRunner(
        gateway=gateway,
        renderer=renderer,
        config=config
        or LayerPlanGlslDirectConfig(
            implementation_identity_sha256=IMPLEMENTATION_SHA256,
            refine_budget=0,
        ),
        receipt_issuer=_TEST_ISSUER,
    )
    return await runner.run(_reference_png(), instruction="match the gray square")


def test_direct_config_requires_trusted_implementation_identity() -> None:
    with pytest.raises(ValueError, match="sha256"):
        LayerPlanGlslDirectConfig(implementation_identity_sha256="unknown")


@pytest.mark.parametrize(
    ("preset", "targets", "refinement_patience"),
    [
        ("fast", (0.08, 0.10), 1),
        ("balanced", (0.06, 0.08), 1),
        ("high", (0.04, 0.06), 1),
        ("manual", (0.03, 0.05), 2),
    ],
)
def test_optimization_policy_owns_quality_target_mapping(
    preset: str,
    targets: tuple[float, float],
    refinement_patience: int,
) -> None:
    policy = DirectOptimizationPolicy.for_quality_preset(preset)

    assert (policy.target_mae, policy.target_loss) == targets
    assert policy.refinement_patience == refinement_patience
    assert len(policy.fingerprint()) == 64
    assert (
        policy.fingerprint()
        == DirectOptimizationPolicy.for_quality_preset(preset).fingerprint()
    )


def test_direct_config_owns_manual_deep_search_budget_mapping() -> None:
    baseline = LayerPlanGlslDirectConfig(
        implementation_identity_sha256=IMPLEMENTATION_SHA256
    )

    for preset in ("fast", "balanced", "high"):
        assert baseline.for_quality_preset(preset) is baseline

    manual = baseline.for_quality_preset("manual")
    assert manual.direct_author_llm_budget == 12
    assert manual.compile_budget == 10
    assert manual.draw_budget == 16
    assert manual.refine_budget == 5
    assert manual.plan_llm_budget == baseline.plan_llm_budget
    assert (
        manual.uniform_tuning_draw_budget == baseline.uniform_tuning_draw_budget
    )
    assert manual.fingerprint() != baseline.fingerprint()


@pytest.mark.anyio
async def test_runner_applies_manual_deep_search_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.app.graphs import layerplan_glsl_direct as graph_module

    captured: dict[str, Any] = {}
    expected_result = object()

    async def fake_graph(**kwargs: Any) -> dict[str, object]:
        captured["context"] = kwargs["context"]
        return {"result": expected_result}

    monkeypatch.setattr(
        graph_module,
        "run_layerplan_glsl_direct_graph",
        fake_graph,
    )
    runner = LayerPlanGlslDirectRunner(
        gateway=_FakeGateway(),
        renderer=_FakeRenderer(),
        config=LayerPlanGlslDirectConfig(
            implementation_identity_sha256=IMPLEMENTATION_SHA256
        ),
        receipt_issuer=_TEST_ISSUER,
    )

    result = await runner.run(_reference_png(), quality_preset="manual")

    assert result is expected_result
    context = captured["context"]
    assert context.config.refine_budget == 5
    assert context.config.direct_author_llm_budget == 12
    assert context.config.compile_budget == 10
    assert context.config.draw_budget == 16
    assert context.config.uniform_tuning_draw_budget == 4
    assert context.optimization_policy.refinement_patience == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target_mae": float("nan")},
        {"target_loss": -0.1},
        {"min_delta_loss": float("inf")},
        {"refinement_patience": -1},
        {"refinement_patience": True},
    ],
)
def test_optimization_policy_rejects_invalid_controls(
    kwargs: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        DirectOptimizationPolicy(**kwargs)


def test_layered_direct_implementation_identity_is_content_addressed() -> None:
    identity = current_layered_direct_glsl_implementation_identity()

    assert identity["schema_version"] == "direct_layered_glsl_implementation_v2"
    assert identity["uniform_optimizer"]["algorithm_version"] == "uniform_coordinate_v2"
    assert identity["authoring_representation"] == "layered_shader_spec_v1"
    assert identity["execution_representation"] == DIRECT_REPRESENTATION
    assert identity == current_layered_direct_glsl_implementation_identity()
    assert len(identity["identity_sha256"]) == 64


@pytest.mark.anyio
async def test_direct_runner_runs_only_layerplan_and_arm_b_initial() -> None:
    gateway = _LayeredFakeGateway()
    renderer = _FakeRenderer()
    result = await _run(gateway, renderer)

    assert result.status == "ok"
    assert [call["role"] for call in gateway.calls] == ["plan", "initial"]
    initial_text = str(gateway.calls[1]["messages"][1].content)
    assert "<canonical_layer_plan>" in initial_text
    assert result.identity.engine_id == DIRECT_ENGINE_ID
    assert result.identity.representation == DIRECT_REPRESENTATION
    assert result.layer_plan is not None
    assert result.current_best is not None
    assert result.current_best.spec.author_identity.plan_sha256 == (
        result.layer_plan.plan_sha256
    )
    assert result.direct_ledger.llm_call_count == 1
    assert result.plan_ledger.llm_call_count == 1
    assert renderer.close_count == 1


@pytest.mark.anyio
async def test_refine_prompt_and_identity_bind_safe_uniform_summary() -> None:
    reference = _reference_png()
    initial = await _run(_LayeredFakeGateway(), _FakeRenderer())
    current_best = initial.current_best
    layer_plan = initial.layer_plan
    assert current_best is not None and layer_plan is not None
    summary = UniformOptimizationSummaryV2(
        base_spec_sha256=current_best.spec.spec_sha256,
        selected_spec_sha256=current_best.spec.spec_sha256,
        config_fingerprint="c" * 64,
        active_component_count=1,
        evaluated_count=2,
        accepted_count=1,
        draw_count=2,
        draw_budget=2,
        initial_loss=current_best.loss + 0.01,
        initial_mae=current_best.mae + 0.02,
        final_loss=current_best.loss,
        final_mae=current_best.mae,
        loss_delta=0.01,
        mae_delta=0.02,
        stop_reason="local_optimum",
    )
    incumbent = ValidatedLayeredIncumbent(
        layered_spec=current_best.layered_spec,
        compiled_program_spec=current_best.spec,
        mae=current_best.mae,
        loss=current_best.loss,
        metrics=current_best.metrics,
        residual_summary=current_best.residual_summary,
    )
    gateway = _LayeredFakeGateway(refine_gains=(0.4,))

    refined = await run_refine_layered_glsl_author(
        gateway=gateway,
        reference_image=reference,
        current_render=current_best.png_bytes,
        incumbent=incumbent,
        layer_plan=layer_plan,
        user_instruction="match the gray square",
        refinement_index=1,
        remaining_refine_budget=1,
        previous_refine_feedback=None,
        uniform_optimization_summary=summary,
        remaining_calls=2,
    )

    context = _tagged_json(gateway.calls[0]["messages"], "refinement_context")
    assert context["uniform_optimization_summary"] == summary.to_safe_dict()
    assert refined.author_identity is not None
    assert refined.author_identity.input_context_sha256 == _refine_context_sha256(
        content_type="image/png",
        current_render=current_best.png_bytes,
        current_render_content_type="image/png",
        incumbent=incumbent,
        plan=layer_plan,
        refinement_index=1,
        remaining_refine_budget=1,
        previous_refine_feedback=None,
        uniform_optimization_summary=summary,
    )


@pytest.mark.anyio
async def test_layered_direct_defers_uniform_capacity_to_renderer() -> None:
    gateway = _FakeGateway(
        initial_responses=[_layered_payload_above_program_spec_uniform_defaults()]
    )
    renderer = _FakeRenderer()

    result = await _run(gateway, renderer)

    assert result.status == "ok"
    assert result.current_best is not None
    assert len(result.current_best.spec.uniform_schema) == 18
    assert result.direct_ledger.compile_count == 1
    assert result.direct_ledger.draw_count == 1
    assert len(renderer.prepare_calls) == 1


@pytest.mark.anyio
async def test_plan_failure_never_starts_direct_author_or_renderer() -> None:
    gateway = _LayeredFakeGateway()
    renderer = _FakeRenderer()
    result = await _run(
        gateway,
        renderer,
        LayerPlanGlslDirectConfig(
            implementation_identity_sha256=IMPLEMENTATION_SHA256,
            plan_llm_budget=0,
            refine_budget=0,
        ),
    )

    assert result.status == "inconclusive"
    assert result.failure_code == "layer_plan_generation_failed"
    assert result.safety_failure_codes == (
        "layer_plan_generation_failed",
        "llm_budget_exhausted",
    )
    assert result.layer_plan is None
    assert result.current_best is None
    assert result.plan_ledger.llm_call_count == 0
    assert result.direct_ledger.llm_call_count == 0
    assert renderer.prepare_calls == []
    assert renderer.draw_calls == []


@pytest.mark.anyio
async def test_initial_failure_is_safe_and_has_no_candidate() -> None:
    gateway = _FakeGateway(initial_responses=["not-json"])
    renderer = _FakeRenderer()
    result = await _run(gateway, renderer)

    assert result.status == "inconclusive"
    assert result.failure_code == "author_output_invalid"
    assert result.safety_failure_codes == ("author_output_invalid",)
    assert result.current_best is None
    assert result.candidates == ()
    assert renderer.prepare_calls == []
    assert renderer.draw_calls == []


@pytest.mark.anyio
async def test_initial_structural_repair_receives_bound_plan_schema() -> None:
    gateway = _LayeredRepairGateway(
        initial_responses=["not-json"],
        repair_responses=[_layered_payload(0.5)],
    )
    result = await _run(gateway, _FakeRenderer())

    assert result.status == "ok"
    assert [call["role"] for call in gateway.calls] == [
        "plan",
        "initial",
        "repair",
    ]
    assert result.direct_ledger.repair_count == 1
    assert gateway.calls[1]["options"].max_output_tokens == 8192
    repair_payload = str(gateway.calls[2]["messages"][1].content)
    assert '"prefixItems"' in repair_payload
    assert '"safe_repair_hints"' in repair_payload
    assert '"layer_id":"bg"' in repair_payload


@pytest.mark.anyio
async def test_static_failure_keeps_private_rule_diagnostics_only() -> None:
    payload = json.loads(_layered_payload(0.5))
    payload["layers"][0]["glsl_body"] = (
        "float value = 0.0;"
        " for (float i = 0.0; i < 4.0; i += 1.0) { value += 0.1; }"
        " return vec4(vec3(value), 1.0);"
    )
    result = await _run(
        _FakeGateway(initial_responses=[json.dumps(payload)]),
        _FakeRenderer(),
    )

    assert result.failure_code == "static_validation_failed"
    assert result.to_private_diagnostics() == [
        {
            "sequence": 2,
            "kind": "initial",
            "error_code": "static_validation_failed",
            "violation_codes": ["unbounded_loop"],
        }
    ]
    assert "glsl_body" not in json.dumps(result.to_private_diagnostics())


@pytest.mark.anyio
async def test_worse_refine_keeps_incumbent_and_closes_program() -> None:
    gateway = _LayeredFakeGateway(
        initial_gains=(0.6,),
        refine_gains=(0.9,),
    )
    renderer = _FakeRenderer()
    result = await _run(
        gateway,
        renderer,
        LayerPlanGlslDirectConfig(
            implementation_identity_sha256=IMPLEMENTATION_SHA256,
            refine_budget=1,
            uniform_tuning_draw_budget=0,
        ),
    )

    assert result.status == "ok"
    assert result.failure_code is None
    assert len(result.candidates) == 2
    assert result.current_best is result.candidates[0]
    assert result.current_best.spec.uniform_values["u_gain"] == 0.6
    assert result.direct_ledger.accepted_candidates == 1
    assert result.direct_ledger.rejected_candidates == 1
    assert result.direct_ledger.compile_count == 1
    assert result.direct_ledger.draw_count == 2
    assert result.direct_ledger.cache_hits == 1
    assert renderer.close_count == 1


@pytest.mark.anyio
async def test_better_single_layer_refine_replaces_incumbent() -> None:
    gateway = _LayeredFakeGateway(
        initial_gains=(0.9,),
        refine_gains=(0.5,),
    )
    result = await _run(
        gateway,
        _FakeRenderer(),
        LayerPlanGlslDirectConfig(
            implementation_identity_sha256=IMPLEMENTATION_SHA256,
            refine_budget=1,
            uniform_tuning_draw_budget=0,
        ),
    )

    assert result.status == "ok"
    assert result.current_best is result.candidates[1]
    assert result.current_best.role == "refine"
    assert result.current_best.patched_layer_id == "bg"
    assert result.current_best.spec.uniform_values["u_gain"] == 0.5
    assert result.direct_ledger.accepted_candidates == 2


@pytest.mark.anyio
async def test_receipt_failure_never_forms_current_best_and_closes_program() -> None:
    renderer = _FakeRenderer(receipt_mode="missing")
    result = await _run(_LayeredFakeGateway(), renderer)

    assert result.status == "inconclusive"
    assert result.failure_code == "static_validation_failed"
    assert result.safety_failure_codes == ("static_validation_failed",)
    assert result.current_best is None
    assert result.direct_ledger.compile_count == 1
    assert result.direct_ledger.draw_count == 1
    assert renderer.close_count == 1
    assert result.to_private_diagnostics() == [
        {
            "sequence": 2,
            "kind": "initial",
            "error_code": "static_validation_failed",
            "detail": "receipt_missing",
        }
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("compile_budget", "draw_budget", "failure_code", "prepare_count", "close_count"),
    [
        (0, 1, "compile_budget_exhausted", 0, 0),
        (1, 0, "draw_budget_exhausted", 1, 1),
    ],
)
async def test_renderer_budget_ceiling_fails_closed_and_releases_resources(
    compile_budget: int,
    draw_budget: int,
    failure_code: str,
    prepare_count: int,
    close_count: int,
) -> None:
    renderer = _FakeRenderer()
    result = await _run(
        _LayeredFakeGateway(),
        renderer,
        LayerPlanGlslDirectConfig(
            implementation_identity_sha256=IMPLEMENTATION_SHA256,
            compile_budget=compile_budget,
            draw_budget=draw_budget,
            refine_budget=0,
        ),
    )

    assert result.status == "inconclusive"
    assert result.failure_code == failure_code
    assert result.safety_failure_codes == (failure_code,)
    assert result.current_best is None
    assert result.direct_ledger.compile_count == prepare_count
    assert result.direct_ledger.draw_count == 0
    assert len(renderer.prepare_calls) == prepare_count
    assert renderer.draw_calls == []
    assert renderer.close_count == close_count


@pytest.mark.anyio
async def test_budget_ledgers_are_independent_and_safe_summary_is_json_only() -> None:
    renderer = _FakeRenderer()
    result = await _run(
        _LayeredFakeGateway(),
        renderer,
        LayerPlanGlslDirectConfig(
            implementation_identity_sha256=IMPLEMENTATION_SHA256,
            direct_author_llm_budget=1,
            compile_budget=1,
            draw_budget=1,
            refine_budget=1,
            plan_llm_budget=1,
        ),
    )

    assert result.status == "ok"
    assert result.plan_ledger.llm_call_count == 1
    assert result.plan_ledger.total_tokens == 15
    assert result.direct_ledger.llm_call_count == 1
    assert result.direct_ledger.total_tokens == 15
    assert result.safety_failure_codes == ()
    assert result.refinement_stop_reason == "target_reached"
    summary = result.to_safe_summary()
    encoded = json.dumps(summary, allow_nan=False)
    assert result.current_best is not None
    assert result.current_best.spec.fragment_source not in encoded
    assert result.current_best.png_bytes.hex() not in encoded
    assert "layer_plan_advisory" not in encoded
    assert (
        summary["current_best"]["layered_spec_sha256"]
        == result.current_best.layered_spec.layered_spec_sha256
    )
    current_best_summary = summary["current_best"]
    assert isinstance(current_best_summary, dict)
    assert current_best_summary["spec_sha256"] == (result.current_best.spec.spec_sha256)
    assert summary["identity"]["implementation_identity_sha256"] == (
        IMPLEMENTATION_SHA256
    )
    with pytest.raises(FrozenInstanceError):
        setattr(result, "status", "inconclusive")
