"""scene_mvp maturity 12/32 单因素重放的纯函数测试。"""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

import scripts.run_scene_mvp_maturity_budget_replay as replay
from scripts.run_scene_mvp_maturity_budget_replay import (
    ARM12_LOCAL_DRAW_BUDGET,
    ARM32_LOCAL_DRAW_BUDGET,
    RendererDrawFailed,
    SceneSnapshot,
    evaluate_budget_gate,
    extra_draws_per_rescue,
    maturity_stage_for_patch,
    run_maturity_arm,
)
from scripts.run_scene_mvp_run_diagnostics import _feature_fixture
from shaderforge.perception import perceive_min_target


def _scene():
    image = Image.new("RGB", (32, 32), "white")
    pixels = image.load()
    assert pixels is not None
    for row in range(5, 27):
        for column in range(5, 27):
            pixels[column, row] = (240, 90, 145)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    anchor = perceive_min_target(buffer.getvalue()).fallback_scene
    return _feature_fixture(
        anchor,
        fixture_name="prefix",
        axes=(0.20, 0.05),
        intensity=0.60,
    )


def _raw_snapshot() -> SceneSnapshot:
    return SceneSnapshot(scene=_scene(), loss=100.0, metrics={"total_loss": 100.0})


def _trace_identity(trace):
    return [
        (
            item["parameter_path"],
            item["direction"],
            item["before"],
            item["after"],
            item["loss"],
        )
        for item in trace
    ]


@pytest.mark.anyio
async def test_first_11_actual_draws_match_when_all_candidates_improve() -> None:
    raw = _raw_snapshot()

    def evaluator():
        call_count = 0

        async def evaluate(scene):
            nonlocal call_count
            call_count += 1
            loss = 100.0 - call_count
            return SceneSnapshot(scene=scene, loss=loss, metrics={"total_loss": loss})

        return evaluate

    arm12 = await run_maturity_arm(
        raw,
        name="arm12",
        local_draw_budget=ARM12_LOCAL_DRAW_BUDGET,
        stage="feature",
        feature_id="diagnostic_prefix",
        evaluate=evaluator(),
    )
    arm32 = await run_maturity_arm(
        raw,
        name="arm32",
        local_draw_budget=ARM32_LOCAL_DRAW_BUDGET,
        stage="feature",
        feature_id="diagnostic_prefix",
        evaluate=evaluator(),
    )

    assert arm12.local_draw_count == 11
    assert arm32.local_draw_count == 31
    assert _trace_identity(arm12.draw_trace) == _trace_identity(arm32.draw_trace[:11])
    assert arm12.best.loss == arm32.prefix_best_loss
    assert replay._scene_sha256(arm12.best.scene) == arm32.prefix_best_scene_sha256


@pytest.mark.anyio
async def test_first_11_actual_draws_match_when_all_candidates_tie() -> None:
    raw = _raw_snapshot()

    async def evaluate(scene):
        return SceneSnapshot(scene=scene, loss=100.0, metrics={"total_loss": 100.0})

    arm12 = await run_maturity_arm(
        raw,
        name="arm12",
        local_draw_budget=11,
        stage="feature",
        feature_id="diagnostic_prefix",
        evaluate=evaluate,
    )
    arm32 = await run_maturity_arm(
        raw,
        name="arm32",
        local_draw_budget=31,
        stage="feature",
        feature_id="diagnostic_prefix",
        evaluate=evaluate,
    )

    assert _trace_identity(arm12.draw_trace) == _trace_identity(arm32.draw_trace[:11])
    assert arm12.tie_reject_count == 11
    assert arm12.best.scene == raw.scene
    assert arm32.prefix_best_scene_sha256 == replay._scene_sha256(raw.scene)


@pytest.mark.anyio
async def test_feature_stage_first_batch_has_production_composition() -> None:
    raw = _raw_snapshot()

    async def evaluate(scene):
        return SceneSnapshot(scene=scene, loss=100.0, metrics={"total_loss": 100.0})

    arm12 = await run_maturity_arm(
        raw,
        name="arm12",
        local_draw_budget=11,
        stage="feature",
        feature_id="diagnostic_prefix",
        evaluate=evaluate,
    )

    assert [item["direction"] for item in arm12.draw_trace[:8]] == ["decrease"] * 8
    assert [
        item["parameter_path"].rsplit(".", 1)[-1] for item in arm12.draw_trace[8:]
    ] == ["center[0]", "center[1]", "axes[0]"]
    assert [item["direction"] for item in arm12.draw_trace[8:]] == ["increase"] * 3


@pytest.mark.anyio
async def test_rebase_skip_does_not_consume_draw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _raw_snapshot()
    calls = 0

    async def evaluate(scene):
        nonlocal calls
        calls += 1
        return SceneSnapshot(scene=scene, loss=99.0, metrics={"total_loss": 99.0})

    monkeypatch.setattr(replay, "rebase_candidate_proposal", lambda *_: None)
    result = await run_maturity_arm(
        raw,
        name="arm12",
        local_draw_budget=11,
        stage="feature",
        feature_id="diagnostic_prefix",
        evaluate=evaluate,
    )

    assert calls == 0
    assert result.local_draw_count == 0
    assert result.clamp_skip_count == 11
    assert result.best == raw


@pytest.mark.anyio
async def test_renderer_failure_stops_arm_and_is_reported() -> None:
    raw = _raw_snapshot()
    calls = 0

    async def evaluate(scene):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RendererDrawFailed("fixture_failure")
        return SceneSnapshot(scene=scene, loss=99.0, metrics={"total_loss": 99.0})

    result = await run_maturity_arm(
        raw,
        name="arm12",
        local_draw_budget=11,
        stage="feature",
        feature_id="diagnostic_prefix",
        evaluate=evaluate,
    )

    assert result.renderer_failed is True
    assert result.renderer_error == "fixture_failure"
    assert result.local_draw_count == 1


@pytest.mark.anyio
async def test_arm32_failure_after_shared_prefix_keeps_11_accounted_draws() -> None:
    raw = _raw_snapshot()
    calls = 0

    async def evaluate(scene):
        nonlocal calls
        calls += 1
        if calls == 12:
            raise RendererDrawFailed("arm32_only_failure")
        loss = 100.0 - calls
        return SceneSnapshot(scene=scene, loss=loss, metrics={"total_loss": loss})

    result = await run_maturity_arm(
        raw,
        name="arm32",
        local_draw_budget=31,
        stage="feature",
        feature_id="diagnostic_prefix",
        evaluate=evaluate,
    )

    assert result.renderer_failed is True
    assert result.renderer_error == "arm32_only_failure"
    assert result.local_draw_count == 11
    assert result.prefix_best_loss == 89.0


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        ("add_feature", ("feature", True)),
        ("replace_feature", ("feature", True)),
        ("replace_color_field", ("color_field", True)),
        ("remove_feature", (None, False)),
    ],
)
def test_patch_operations_map_to_production_maturity_stage(
    operation: str,
    expected: tuple[str | None, bool],
) -> None:
    assert maturity_stage_for_patch(operation) == expected


def test_unknown_patch_operation_is_rejected() -> None:
    with pytest.raises(ValueError, match="未知 Patch operation"):
        maturity_stage_for_patch("mutate_everything")


def _gate_case(
    *,
    arm12: float,
    arm32: float,
    rescued: bool,
    objective_delta: float = 0.0,
    roi_delta: float = 0.0,
) -> dict[str, object]:
    return {
        "arm12_loss": arm12,
        "arm32_loss": arm32,
        "arm32_objective_delta": objective_delta,
        "arm32_max_roi_delta": roi_delta,
        "rescued_by_32": rescued,
        "renderer_failed": False,
    }


def test_gate_supports_32_only_for_nonempty_clean_rescue() -> None:
    result = evaluate_budget_gate(
        [
            _gate_case(arm12=0.05, arm32=0.04, rescued=True),
            _gate_case(arm12=0.04, arm32=0.039, rescued=False),
        ]
    )
    assert result["outcome"] == "budget32_supported"
    assert result["clean_rescue_count"] == 1


def test_gate_keeps_12_when_there_is_no_clean_rescue() -> None:
    result = evaluate_budget_gate(
        [
            _gate_case(arm12=0.05, arm32=0.05, rescued=False),
            _gate_case(arm12=0.04, arm32=0.04, rescued=False),
        ]
    )
    assert result["outcome"] == "budget12_supported"


def test_extra_draw_cost_counts_only_rescued_cases() -> None:
    assert (
        extra_draws_per_rescue(
            [
                {"rescued_by_32": False, "extra_local_draws": 20},
                {"rescued_by_32": True, "extra_local_draws": 20},
            ]
        )
        == 20.0
    )
    assert (
        extra_draws_per_rescue([{"rescued_by_32": False, "extra_local_draws": 20}])
        is None
    )


def test_gate_is_inconclusive_for_mixed_clean_and_harmful_rescue() -> None:
    result = evaluate_budget_gate(
        [
            _gate_case(arm12=0.05, arm32=0.04, rescued=True),
            _gate_case(
                arm12=0.06,
                arm32=0.04,
                rescued=True,
                roi_delta=0.02,
            ),
        ]
    )
    assert result["outcome"] == "inconclusive"


@pytest.mark.parametrize(
    "bad_case",
    [
        {"arm12_loss": 0.1},
        _gate_case(arm12=0.1, arm32=float("nan"), rescued=False),
        {
            **_gate_case(arm12=0.1, arm32=0.09, rescued=True),
            "renderer_failed": True,
        },
    ],
)
def test_gate_fails_closed_on_missing_nonfinite_or_renderer_failure(
    bad_case: dict[str, object],
) -> None:
    result = evaluate_budget_gate([bad_case])
    assert result["outcome"] == "inconclusive"
    assert result["reason"] == "missing_or_failed_gate_field"
