"""固定 fake LLM/Renderer 的 direct attempt 全内存链集成验收."""

from __future__ import annotations

import json
from importlib import import_module
from typing import Any

import pytest

from agent.app.services.layerplan_glsl_direct import (
    LayerPlanGlslDirectConfig,
    LayerPlanGlslDirectRunner,
)
from shaderforge.program_spec import is_executable

_shadow_fakes: Any = import_module("tests.unit_tests.test_layerplan_glsl_shadow_runner")
_FakeGateway = _shadow_fakes._FakeGateway
_FakeRenderer = _shadow_fakes._FakeRenderer
_TEST_ISSUER = _shadow_fakes._TEST_ISSUER
_reference_png = _shadow_fakes._reference_png


@pytest.mark.anyio
async def test_fake_llm_renderer_direct_chain_retains_private_canonical_result() -> (
    None
):
    """覆盖 plan → Initial → safety → receipt → metric → immutable result."""
    gateway = _FakeGateway()
    renderer = _FakeRenderer()
    runner = LayerPlanGlslDirectRunner(
        gateway=gateway,
        renderer=renderer,
        config=LayerPlanGlslDirectConfig(
            implementation_identity_sha256="b" * 64,
            plan_llm_budget=1,
            direct_author_llm_budget=1,
            compile_budget=1,
            draw_budget=1,
            refine_budget=0,
        ),
        receipt_issuer=_TEST_ISSUER,
    )

    result = await runner.run(_reference_png(), instruction="match")

    assert result.status == "ok"
    assert result.layer_plan is not None
    assert result.current_best is not None
    assert result.current_best.spec.validation_attestation is not None
    assert is_executable(result.current_best.spec, issuer=_TEST_ISSUER)
    assert result.current_best.metrics["metric_version"] == "min_scene_composite_v3"
    assert result.current_best.rgb_bytes
    assert result.current_best.png_bytes
    assert result.direct_ledger.compile_count == 1
    assert result.direct_ledger.draw_count == 1
    assert renderer.close_count == 1
    json.dumps(result.to_safe_summary(), allow_nan=False)
