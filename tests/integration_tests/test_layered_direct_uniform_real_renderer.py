"""Real Chromium WebGL1 coverage for manifest-only uniform optimization."""

from __future__ import annotations

import json
from collections.abc import Mapping
from hashlib import sha256
from typing import Any

import pytest

from agent.app.graphs.layerplan_glsl_direct import (
    DirectGraphContext,
    run_layerplan_glsl_direct_graph,
)
from agent.app.services.layerplan_glsl_direct import LayerPlanGlslDirectConfig
from shaderforge.program_spec import (
    canonical_json,
    is_executable,
    process_receipt_verifier,
)
from shaderforge.rendering import (
    PlaywrightWebGL1Renderer,
    PreparedWebGL1Renderer,
)
from tests.direct_fakes import FakeGateway, reference_png

IMPLEMENTATION_SHA256 = "d" * 64
UNIFORM_DRAW_BUDGET = 4


def _multi_parameter_layered_payload() -> str:
    """Return one deterministic float + vec3 manifest-driven shader."""
    return json.dumps(
        {
            "schema_version": "layered_shader_spec_v1",
            "canvas": {"width": 64, "height": 64},
            "layers": [
                {
                    "layer_id": "bg",
                    "role": "background",
                    "z_index": 0,
                    "glsl_body": (
                        "float tint = "
                        "(u_tint.r + u_tint.g + u_tint.b) / 3.0;\n"
                        "float value = (u_gain + tint) / 2.0;\n"
                        "return vec4(vec3(value), 1.0);"
                    ),
                    "uniform_schema": {
                        "u_gain": {
                            "type": "float",
                            "minimum": 0.0,
                            "maximum": 1.0,
                            "default": 0.2,
                        },
                        "u_tint": {
                            "type": "vec3",
                            "minimum": [0.0, 0.0, 0.0],
                            "maximum": [1.0, 1.0, 1.0],
                            "default": [0.2, 0.2, 0.2],
                        },
                    },
                    "uniform_values": {
                        "u_gain": 0.2,
                        "u_tint": [0.2, 0.2, 0.2],
                    },
                    "tunable_manifest": [
                        {
                            "path": "u_gain",
                            "type": "float",
                            "minimum": 0.0,
                            "maximum": 1.0,
                            "step": 0.1,
                        },
                        {
                            "path": "u_tint",
                            "type": "vec3",
                            "minimum": [0.0, 0.0, 0.0],
                            "maximum": [1.0, 1.0, 1.0],
                            "step": 0.1,
                        },
                    ],
                }
            ],
        }
    )


class _TrackingPlaywrightWebGL1Renderer(PlaywrightWebGL1Renderer):
    """Expose only lifecycle counts needed to prove real prepared-program reuse."""

    def __init__(self) -> None:
        super().__init__()
        self.prepare_count = 0
        self.close_count = 0
        self.prepared_history: list[PreparedWebGL1Renderer] = []

    async def prepare(
        self,
        fragment_source: str,
        width: int,
        height: int,
        uniform_schema: Mapping[str, Any],
    ) -> PreparedWebGL1Renderer:
        self.prepare_count += 1
        prepared = await super().prepare(
            fragment_source,
            width,
            height,
            uniform_schema,
        )
        self.prepared_history.append(prepared)
        return prepared

    async def close(self) -> None:
        self.close_count += 1
        await super().close()


@pytest.mark.anyio
async def test_multi_parameter_uniform_search_uses_real_webgl1_full_chain() -> None:
    """Bind every optimized candidate to a fresh real draw and proof chain."""
    gateway = FakeGateway(initial_responses=[_multi_parameter_layered_payload()])
    renderer = _TrackingPlaywrightWebGL1Renderer()
    verifier = process_receipt_verifier()
    context = DirectGraphContext(
        gateway=gateway,
        renderer=renderer,
        config=LayerPlanGlslDirectConfig(
            implementation_identity_sha256=IMPLEMENTATION_SHA256,
            plan_llm_budget=1,
            direct_author_llm_budget=1,
            compile_budget=1,
            # Leave one global slot unused so the uniform-scoped budget owns
            # the deterministic terminal reason.
            draw_budget=2 + UNIFORM_DRAW_BUDGET,
            refine_budget=0,
            uniform_tuning_draw_budget=UNIFORM_DRAW_BUDGET,
            uniform_tuning_active_component_cap=4,
            uniform_tuning_max_passes=1,
        ),
        receipt_issuer=verifier,
    )

    graph_released_programs = False
    try:
        output = await run_layerplan_glsl_direct_graph(
            reference_image=reference_png(gray=179),
            content_type="image/png",
            instruction="match the locally generated gray reference",
            context=context,
        )
        graph_released_programs = (
            not context.program_cache
            and len(renderer.prepared_history) == 1
            and renderer.prepared_history[0]._closed
            and not renderer._prepared
        )
    finally:
        await renderer.close()

    result = output["result"]
    initial = next(
        candidate for candidate in result.candidates if candidate.role == "initial"
    )
    selected = result.current_best
    assert result.status == "ok"
    assert selected is not None
    assert selected.role == "uniform_optimize"

    assert len(initial.spec.tunable_manifest) == 2
    assert {item.type for item in initial.spec.tunable_manifest} == {"float", "vec3"}
    assert selected.spec.source_sha256 == initial.spec.source_sha256
    assert selected.spec.binding_sha256 != initial.spec.binding_sha256
    assert selected.spec.spec_sha256 != initial.spec.spec_sha256
    assert selected.layered_spec.derivation_provenance is not None
    assert selected.spec.derivation_provenance is not None

    initial_attestation = initial.spec.validation_attestation
    selected_attestation = selected.spec.validation_attestation
    assert initial_attestation is not None
    assert selected_attestation is not None
    initial_receipt = initial_attestation.receipt
    selected_receipt = selected_attestation.receipt
    assert selected_receipt.spec_sha256 == selected.spec.spec_sha256
    assert selected_receipt.source_sha256 == selected.spec.source_sha256
    assert selected_receipt.digest != initial_receipt.digest
    assert selected_receipt.nonce != initial_receipt.nonce
    assert verifier.verify(selected_receipt)
    assert is_executable(selected.spec, issuer=verifier)
    attestations = [
        candidate.spec.validation_attestation for candidate in result.candidates
    ]
    assert all(attestation is not None for attestation in attestations)
    receipts = [
        attestation.receipt
        for attestation in attestations
        if attestation is not None
    ]
    assert len(receipts) == len(result.candidates)
    assert len({receipt.nonce for receipt in receipts}) == len(receipts)
    assert all(verifier.verify(receipt) for receipt in receipts)
    assert all(
        is_executable(candidate.spec, issuer=verifier)
        for candidate in result.candidates
    )

    ledger = result.direct_ledger
    assert ledger.compile_count == 1
    assert ledger.draw_count == 1 + UNIFORM_DRAW_BUDGET
    assert ledger.cache_hits == UNIFORM_DRAW_BUDGET
    assert ledger.uniform_tuning_draw_count == UNIFORM_DRAW_BUDGET
    assert ledger.uniform_tuning_evaluated_count == UNIFORM_DRAW_BUDGET
    assert ledger.uniform_tuning_accepted_count >= 1
    assert ledger.uniform_tuning_session_count == 1
    assert ledger.uniform_tuning_active_component_count == 4

    summary = result.uniform_optimization_summary
    assert summary is not None
    assert summary.base_spec_sha256 == initial.spec.spec_sha256
    assert summary.selected_spec_sha256 == selected.spec.spec_sha256
    assert summary.active_component_count == 4
    assert summary.draw_count == UNIFORM_DRAW_BUDGET
    assert summary.evaluated_count == ledger.uniform_tuning_evaluated_count
    assert summary.accepted_count == ledger.uniform_tuning_accepted_count
    assert summary.final_loss == pytest.approx(selected.loss)
    assert summary.final_mae == pytest.approx(selected.mae)
    assert summary.final_loss < summary.initial_loss
    assert summary.final_mae < summary.initial_mae
    assert summary.loss_delta > 0
    assert summary.mae_delta > 0
    assert summary.stop_reason == "local_optimum"
    assert summary.private_trace_sha256 is not None
    assert len(result.uniform_optimization_trace) == summary.evaluated_count
    assert summary.private_trace_sha256 == sha256(
        canonical_json(list(result.uniform_optimization_trace)).encode("utf-8")
    ).hexdigest()
    assert all(
        item["candidate_spec_sha256"] is not None
        and item["failure_code"] is None
        for item in result.uniform_optimization_trace
    )
    assert result.refinement_stop_reason == "refine_budget_exhausted"

    assert renderer.prepare_count == 1
    assert renderer.prepared_history[0].render_count == ledger.draw_count
    assert graph_released_programs
    assert "record_uniform_outcome" in output["completed_nodes"]
    assert output["completed_nodes"][-2:] == (
        "release_resources",
        "finalize_attempt",
    )
    assert renderer.close_count == 1
    assert renderer._page is None
    assert renderer._browser is None
    assert renderer._playwright is None
