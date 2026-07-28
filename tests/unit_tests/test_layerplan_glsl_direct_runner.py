"""LayerPlan/direct GLSL 单 engine runner 的隔离与失败收敛测试."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from agent.app.services.layerplan_glsl_direct import (
    DIRECT_ENGINE_ID,
    DIRECT_REPRESENTATION,
    DirectAttemptResult,
    LayerPlanGlslDirectConfig,
    LayerPlanGlslDirectRunner,
    current_layered_direct_glsl_implementation_identity,
)
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


def test_layered_direct_implementation_identity_is_content_addressed() -> None:
    identity = current_layered_direct_glsl_implementation_identity()

    assert identity["schema_version"] == "direct_layered_glsl_implementation_v1"
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
        initial_gains=(0.5,),
        refine_gains=(0.9,),
    )
    renderer = _FakeRenderer()
    result = await _run(
        gateway,
        renderer,
        LayerPlanGlslDirectConfig(
            implementation_identity_sha256=IMPLEMENTATION_SHA256,
            refine_budget=1,
        ),
    )

    assert result.status == "ok"
    assert result.failure_code is None
    assert len(result.candidates) == 2
    assert result.current_best is result.candidates[0]
    assert result.current_best.spec.uniform_values["u_gain"] == 0.5
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
    assert result.safety_failure_codes == ("llm_budget_exhausted",)
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
