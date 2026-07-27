"""LayerPlan/direct GLSL 单 engine runner 的隔离与失败收敛测试."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from importlib import import_module
from typing import Any

import pytest

from agent.app.services.layerplan_glsl_direct import (
    DIRECT_ENGINE_ID,
    DIRECT_REPRESENTATION,
    DirectAttemptResult,
    LayerPlanGlslDirectConfig,
    LayerPlanGlslDirectRunner,
)
from agent.app.services.layerplan_glsl_shadow import ShadowABConfigError

_shadow_fakes: Any = import_module("tests.unit_tests.test_layerplan_glsl_shadow_runner")
_FakeGateway = _shadow_fakes._FakeGateway
_FakeRenderer = _shadow_fakes._FakeRenderer
_TEST_ISSUER = _shadow_fakes._TEST_ISSUER
_reference_png = _shadow_fakes._reference_png
_spec_payload = _shadow_fakes._spec_payload

IMPLEMENTATION_SHA256 = "a" * 64


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
    with pytest.raises(ShadowABConfigError, match="SHA-256"):
        LayerPlanGlslDirectConfig(implementation_identity_sha256="unknown")


@pytest.mark.anyio
async def test_direct_runner_runs_only_layerplan_and_arm_b_initial() -> None:
    gateway = _FakeGateway()
    renderer = _FakeRenderer()
    result = await _run(gateway, renderer)

    assert result.status == "ok"
    assert [call["role"] for call in gateway.calls] == ["plan", "initial"]
    initial_text = str(gateway.calls[1]["messages"][1].content)
    assert "<layer_plan_advisory>" in initial_text
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
async def test_plan_failure_never_starts_direct_author_or_renderer() -> None:
    gateway = _FakeGateway()
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
    gateway = _FakeGateway(initial_responses_b=["not-json"])
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
async def test_worse_refine_keeps_incumbent_and_closes_program() -> None:
    gateway = _FakeGateway(
        initial_responses_b=[_spec_payload(0.5)],
        refine_responses_b=[_spec_payload(0.9)],
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
async def test_receipt_failure_never_forms_current_best_and_closes_program() -> None:
    renderer = _FakeRenderer(receipt_mode="missing")
    result = await _run(_FakeGateway(), renderer)

    assert result.status == "inconclusive"
    assert result.failure_code == "static_validation_failed"
    assert result.safety_failure_codes == ("static_validation_failed",)
    assert result.current_best is None
    assert result.direct_ledger.compile_count == 1
    assert result.direct_ledger.draw_count == 1
    assert renderer.close_count == 1


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
        _FakeGateway(),
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
        _FakeGateway(),
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
    current_best_summary = summary["current_best"]
    assert isinstance(current_best_summary, dict)
    assert current_best_summary["spec_sha256"] == (result.current_best.spec.spec_sha256)
    assert summary["identity"]["implementation_identity_sha256"] == (
        IMPLEMENTATION_SHA256
    )
    with pytest.raises(FrozenInstanceError):
        setattr(result, "status", "inconclusive")
