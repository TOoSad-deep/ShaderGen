"""锁定生产 scene_mvp acceptance 语义：仅 strict total-loss 严格改善。

D065：生产不存在 geometry-first 字典序 acceptance；本文件用真实
`min_scene_composite_v3` scorer 与节点级回归固定这一事实。
"""

from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageDraw

from agent.app.nodes.png_to_shader_min import MinRendererRegistry, make_min_nodes
from shaderforge.evaluation import evaluate_min_scene
from shaderforge.optimization import (
    accepts_strict_total_loss,
    propose_min_scene_candidates,
)
from shaderforge.perception import perceive_min_target
from shaderforge.store import LocalArtifactStore

_BACKGROUND = (1.0, 1.0, 1.0)
_TARGET_COLOR = np.array([0.9, 0.3, 0.5])


def _square_image(x0: int, y0: int, size: int, color: np.ndarray) -> np.ndarray:
    image = np.full((64, 64, 3), _BACKGROUND, dtype=np.float32)
    image[y0 : y0 + size, x0 : x0 + size] = color
    return image


_TARGET = _square_image(24, 24, 16, _TARGET_COLOR)
# 几何偏移 1px、颜色正确：geometry 分量较差，total 较低。
_OFFSET_TRUE_COLOR = _square_image(23, 24, 16, _TARGET_COLOR)
# 几何完全对齐、颜色偏离：geometry 分量为 0，total 较高。
_ALIGNED_WRONG_COLOR = _square_image(24, 24, 16, _TARGET_COLOR + (-0.2, 0.2, -0.1))


def test_geometry_improvement_cannot_override_total_loss_regression() -> None:
    incumbent = evaluate_min_scene(_TARGET, _OFFSET_TRUE_COLOR, _BACKGROUND)
    candidate = evaluate_min_scene(_TARGET, _ALIGNED_WRONG_COLOR, _BACKGROUND)
    assert candidate.geometry_mask_loss < incumbent.geometry_mask_loss
    assert candidate.total_loss > incumbent.total_loss
    # geometry-first 字典序会接受该候选，生产 acceptance 必须拒绝。
    assert (candidate.geometry_mask_loss, candidate.total_loss) < (
        incumbent.geometry_mask_loss,
        incumbent.total_loss,
    )
    assert not accepts_strict_total_loss(candidate.total_loss, incumbent.total_loss)


def test_strict_total_improvement_accepts_despite_geometry_regression() -> None:
    incumbent = evaluate_min_scene(_TARGET, _ALIGNED_WRONG_COLOR, _BACKGROUND)
    candidate = evaluate_min_scene(_TARGET, _OFFSET_TRUE_COLOR, _BACKGROUND)
    assert candidate.geometry_mask_loss > incumbent.geometry_mask_loss
    assert candidate.total_loss < incumbent.total_loss
    # geometry-first 字典序会拒绝该候选，生产 acceptance 必须接受。
    assert not (candidate.geometry_mask_loss, candidate.total_loss) < (
        incumbent.geometry_mask_loss,
        incumbent.total_loss,
    )
    assert accepts_strict_total_loss(candidate.total_loss, incumbent.total_loss)


def test_total_loss_tie_is_rejected() -> None:
    metric = evaluate_min_scene(_TARGET, _OFFSET_TRUE_COLOR, _BACKGROUND)
    assert not accepts_strict_total_loss(metric.total_loss, metric.total_loss)


def test_invalid_candidate_or_incumbent_loss_fails_closed() -> None:
    invalid_losses = (float("nan"), float("inf"), float("-inf"), -0.1)
    for invalid_loss in invalid_losses:
        assert not accepts_strict_total_loss(invalid_loss, 0.1)
        assert not accepts_strict_total_loss(0.05, invalid_loss)


class _WhitePrepared:
    def __init__(self) -> None:
        self.width = 0
        self.height = 0
        self.prepare_duration_ms = 1.0
        self.render_durations_ms: tuple[float, ...] = ()

    @property
    def render_count(self) -> int:
        return len(self.render_durations_ms)

    async def render_uniforms(self, _values, *, capture_png=False):
        self.render_durations_ms = (*self.render_durations_ms, 1.0)
        rgb = Image.new("RGB", (self.width, self.height), "white").tobytes()
        return SimpleNamespace(
            success=True,
            rgb_bytes=rgb,
            image_bytes=None,
            draw_error=None,
        )

    async def close(self) -> None:
        return None


class _FailingPrepared(_WhitePrepared):
    async def render_uniforms(self, _values, *, capture_png=False):
        self.render_durations_ms = (*self.render_durations_ms, 1.0)
        return SimpleNamespace(
            success=False,
            rgb_bytes=None,
            image_bytes=None,
            draw_error="synthetic_draw_failure",
        )


class _FakeRenderer:
    prepared_class = _WhitePrepared

    def __init__(self) -> None:
        self.prepared = self.prepared_class()

    async def prepare(self, _source, width, height, _uniform_schema):
        self.prepared.width = width
        self.prepared.height = height
        return self.prepared

    async def close(self) -> None:
        return None


class _FailingRenderer(_FakeRenderer):
    prepared_class = _FailingPrepared


class _UnusedGateway:
    async def ainvoke(self, _messages, _options):
        raise AssertionError("optimize_base 不得调用模型")


def _pink_orb_png() -> bytes:
    image = Image.new("RGB", (96, 96), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((14, 14, 82, 82), fill=(245, 80, 130))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _optimize_base_state(render_budget: int) -> dict[str, object]:
    perception = perceive_min_target(_pink_orb_png())
    scene = perception.fallback_scene
    white_render = np.full(
        (scene.canvas.height, scene.canvas.width, 3),
        _BACKGROUND,
        dtype=np.float32,
    )
    metric = evaluate_min_scene(
        perception.target_rgb, white_render, scene.canvas.background
    )
    incumbent = {
        "scene": scene.model_dump(mode="json"),
        "mae": metric.global_mae,
        "loss": metric.total_loss,
        "metrics": metric.to_dict(),
        "residual_summary": {},
        "glsl": "incumbent-glsl",
        "render": b"incumbent-png",
    }
    return {
        "project_id": "acceptance-project",
        "run_id": "acceptance-run",
        "scene": incumbent["scene"],
        "current_best": incumbent,
        "target_rgb": perception.target_rgb,
        "metric_background": scene.canvas.background,
        "render_count": 0,
        "render_budget": render_budget,
        "trace": (),
    }


@pytest.mark.anyio
@pytest.mark.parametrize("render_budget", (32, 5))
async def test_optimize_base_rejects_total_loss_ties_with_unchanged_budget(
    tmp_path, render_budget: int
) -> None:
    # 均匀白色渲染使每个候选与 incumbent total_loss 完全持平，必须全部拒绝。
    state = _optimize_base_state(render_budget)
    nodes = make_min_nodes(
        LocalArtifactStore(tmp_path),
        MinRendererRegistry(_FakeRenderer),  # type: ignore[arg-type]
        _UnusedGateway(),  # type: ignore[arg-type]
    )

    update = await nodes["optimize_base"](state)

    expected = len(
        propose_min_scene_candidates(
            perceive_min_target(_pink_orb_png()).fallback_scene,
            stage="base",
            remaining_draw_budget=render_budget,
            batch_size=32,
        )
    )
    assert update["render_count"] == expected
    assert update["current_best"] == state["current_best"]
    assert update["current_best_loss"] == state["current_best"]["loss"]  # type: ignore[index]
    trace = update["trace"][-1]  # type: ignore[index]
    assert trace["phase"] == "optimize_base"
    assert "rolled_back" in trace["message"]
    assert trace["candidates_evaluated"] == expected
    assert trace["accepted_parameter"] is None


@pytest.mark.anyio
async def test_optimize_base_renderer_failure_never_pollutes_incumbent(
    tmp_path,
) -> None:
    state = _optimize_base_state(32)
    nodes = make_min_nodes(
        LocalArtifactStore(tmp_path),
        MinRendererRegistry(_FailingRenderer),  # type: ignore[arg-type]
        _UnusedGateway(),  # type: ignore[arg-type]
    )

    update = await nodes["optimize_base"](state)

    # 失败候选仍计入 draw 预算（fail-closed 记账），但不得污染 incumbent。
    assert update["render_count"] > 0
    assert update["current_best"] == state["current_best"]
    assert update["current_best_loss"] == state["current_best"]["loss"]  # type: ignore[index]
    trace = update["trace"][-1]  # type: ignore[index]
    assert "rolled_back" in trace["message"]
    assert trace["accepted_parameter"] is None
