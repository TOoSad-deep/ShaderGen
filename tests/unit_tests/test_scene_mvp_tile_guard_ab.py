"""固定 7 例 tile guard A/B 脚本的纯函数测试。"""

from __future__ import annotations

import numpy as np
import pytest

from scripts.run_scene_mvp_tile_guard_ab import (
    GuardCandidate,
    TileRegression,
    guard_accepts,
    max_tile_regression,
    replay_guard_arm,
    tile_mae_grid,
)


def _solid_image(width: int = 8, height: int = 8, value: float = 1.0) -> np.ndarray:
    return np.full((height, width, 3), value, dtype=np.float32)


def test_tile_mae_grid_matches_manual_tile_means() -> None:
    reference = _solid_image(8, 8, 0.0)
    rendered = _solid_image(8, 8, 0.0)
    rendered[:4, :4] = 0.4
    rendered[4:, 4:] = 0.2

    result = tile_mae_grid(reference, rendered, 2)

    assert result.shape == (2, 2)
    assert result[0, 0] == pytest.approx(0.4)
    assert result[0, 1] == pytest.approx(0.0)
    assert result[1, 0] == pytest.approx(0.0)
    assert result[1, 1] == pytest.approx(0.2)


def test_tile_mae_grid_rejects_invalid_input() -> None:
    reference = _solid_image(8, 8, 0.0)
    with pytest.raises(ValueError, match="相同尺寸"):
        tile_mae_grid(reference, _solid_image(4, 8, 0.0), 2)
    with pytest.raises(ValueError, match="正整数"):
        tile_mae_grid(reference, reference.copy(), 0)
    with pytest.raises(ValueError, match="不小于"):
        tile_mae_grid(reference, reference.copy(), 16)


def test_max_tile_regression_reports_worst_tile_and_location() -> None:
    reference = _solid_image(16, 16, 0.0)
    incumbent = _solid_image(16, 16, 0.0)
    candidate = _solid_image(16, 16, 0.0)
    # 只在 8x8 网格的 (1, 2) tile 引入误差，对应像素行 2-3、列 4-5。
    candidate[2:4, 4:6] = 0.5

    regression = max_tile_regression(reference, incumbent, candidate)

    assert regression.value == pytest.approx(0.5)
    assert regression.grid == 8
    assert regression.row == 1
    assert regression.column == 2


def test_max_tile_regression_negative_when_candidate_improves() -> None:
    reference = _solid_image(16, 16, 0.0)
    incumbent = _solid_image(16, 16, 0.3)
    candidate = _solid_image(16, 16, 0.1)

    regression = max_tile_regression(reference, incumbent, candidate)

    assert regression.value == pytest.approx(-0.2)


def test_max_tile_regression_requires_at_least_one_grid() -> None:
    image = _solid_image(8, 8, 0.0)
    with pytest.raises(ValueError, match="至少需要一个 grid"):
        max_tile_regression(image, image.copy(), image.copy(), grids=())


def test_guard_accepts_combines_total_loss_and_tile_tolerance() -> None:
    regression = TileRegression(value=0.004, grid=4, row=0, column=0)
    assert guard_accepts(
        incumbent_total_loss=0.5,
        candidate_total_loss=0.4,
        regression=regression,
        tolerance=0.005,
    )
    assert not guard_accepts(
        incumbent_total_loss=0.5,
        candidate_total_loss=0.4,
        regression=regression,
        tolerance=0.0025,
    )
    assert not guard_accepts(
        incumbent_total_loss=0.5,
        candidate_total_loss=0.5,
        regression=TileRegression(value=-1.0, grid=4, row=0, column=0),
        tolerance=0.005,
    )
    with pytest.raises(ValueError, match="不能为负"):
        guard_accepts(
            incumbent_total_loss=0.5,
            candidate_total_loss=0.4,
            regression=regression,
            tolerance=-0.1,
        )


def _stream_fixture() -> tuple[np.ndarray, np.ndarray, list[GuardCandidate]]:
    reference = _solid_image(16, 16, 0.0)
    baseline = _solid_image(16, 16, 0.2)
    # 候选 1：整体改善但 8x8 (0, 0) tile 回退 0.01；Arm A 接受。
    candidate_one = baseline.copy()
    candidate_one[8:, :] = 0.0
    candidate_one[0:2, 0:2] = 0.21
    # 候选 2：无 tile 回退的小幅整体改善；Arm A 接受。
    candidate_two = _solid_image(16, 16, 0.19)
    candidates = [
        GuardCandidate(
            total_loss=0.40,
            rgb=candidate_one,
            accepted_by_a=True,
            label="base:object.primitive.center[0]:decrease",
        ),
        GuardCandidate(
            total_loss=0.39,
            rgb=candidate_two,
            accepted_by_a=True,
            label="base:object.primitive.axes[0]:increase",
        ),
    ]
    return reference, baseline, candidates


def test_replay_guard_arm_tolerance_zero_blocks_tile_regression() -> None:
    reference, baseline, candidates = _stream_fixture()

    result = replay_guard_arm(reference, baseline, 0.5, candidates, 0.0)

    assert result.accepted_count == 1
    assert result.rejected_tile_guard_count == 1
    assert result.a_accepted_guard_rejected_count == 1
    assert result.final_index == 1
    assert result.max_blocked_regression is not None
    assert result.max_blocked_regression.value == pytest.approx(0.01)
    assert result.max_blocked_regression.grid == 8
    assert (
        result.max_blocked_regression.row,
        result.max_blocked_regression.column,
    ) == (0, 0)


def test_replay_guard_arm_large_tolerance_matches_arm_a() -> None:
    reference, baseline, candidates = _stream_fixture()

    result = replay_guard_arm(reference, baseline, 0.5, candidates, 0.5)

    assert result.accepted_count == 2
    assert result.rejected_tile_guard_count == 0
    assert result.a_accepted_guard_rejected_count == 0
    assert result.final_index == 1


def test_replay_guard_arm_boundary_tolerance_is_inclusive() -> None:
    reference, baseline, candidates = _stream_fixture()

    result = replay_guard_arm(reference, baseline, 0.5, candidates, 0.01)

    # 候选 1 的最大回退恰好等于容差，按声明的“不超过容差”接受；
    # 候选 2 相对候选 1 在下半图回退 0.19，被 guard 拦截。
    assert result.steps[0].accepted
    assert result.steps[1].reason == "tile_guard_rejected"
    assert result.final_index == 0


def test_replay_guard_arm_counts_total_loss_rejections() -> None:
    reference = _solid_image(16, 16, 0.0)
    baseline = _solid_image(16, 16, 0.2)
    worse = GuardCandidate(
        total_loss=0.60,
        rgb=_solid_image(16, 16, 0.0),
        accepted_by_a=False,
        label="base:object.primitive.center[1]:increase",
    )

    result = replay_guard_arm(reference, baseline, 0.5, [worse], 0.01)

    assert result.accepted_count == 0
    assert result.rejected_total_loss_count == 1
    assert result.rejected_tile_guard_count == 0
    assert result.final_index is None
    assert result.max_blocked_regression is None
    assert result.final_regression_vs_baseline.value == pytest.approx(0.0)


def test_replay_guard_arm_tracks_diverged_incumbent() -> None:
    # Arm B 拒绝第一个候选后，后续候选的 guard 输入是相对 fallback
    # incumbent 计算，而不是相对 Arm A 的 incumbent。
    reference = _solid_image(16, 16, 0.0)
    baseline = _solid_image(16, 16, 0.2)
    blocked = baseline.copy()
    blocked[0:2, 0:2] = 1.0
    blocked[8:, :] = 0.0
    followers = _solid_image(16, 16, 0.15)
    candidates = [
        GuardCandidate(
            total_loss=0.40,
            rgb=blocked,
            accepted_by_a=True,
            label="base:object.primitive.center[0]:decrease",
        ),
        GuardCandidate(
            total_loss=0.45,
            rgb=followers,
            accepted_by_a=False,
            label="base:object.primitive.axes[1]:increase",
        ),
    ]

    strict = replay_guard_arm(reference, baseline, 0.5, candidates, 0.0)
    relaxed = replay_guard_arm(reference, baseline, 0.5, candidates, 1.0)

    # strict：候选 1 被 guard 拦截后，候选 2 相对 fallback 改善、被接受。
    assert strict.final_index == 1
    assert strict.accepted_count == 1
    # relaxed：候选 1 被接受后，候选 2 不再改善、被拒绝。
    assert relaxed.final_index == 0
    assert relaxed.accepted_count == 1
    assert relaxed.rejected_total_loss_count == 1
