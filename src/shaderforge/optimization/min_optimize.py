"""为 scene_mvp 提议小批、确定性的参数邻域候选。."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

from shaderforge.scene import MinScene

OptimizationStage = Literal["base", "feature"]

# 单次调用始终是小预算邻域搜索；较大的 run 预算由调用方分批调度。
MAX_CANDIDATES_PER_BATCH = 32
_DEFAULT_BASE_BATCH_SIZE = 32
_DEFAULT_FEATURE_BATCH_SIZE = 16


@dataclass(frozen=True)
class TunableParameter:
    """描述一个白名单数值参数及其确定性邻域。."""

    path: str
    minimum: float
    maximum: float
    step: float


@dataclass(frozen=True)
class CandidateProposal:
    """携带可读变更原因与完整合法 scene 的单参数候选。."""

    stage: OptimizationStage
    parameter: TunableParameter
    direction: Literal["decrease", "increase"]
    before: float
    after: float
    scene: MinScene
    feature_id: str | None = None


@dataclass(frozen=True)
class ScoredScene:
    """供调用方串行维护严格单调 MAE 的轻量快照。."""

    scene: MinScene
    mae: float

    def __post_init__(self) -> None:
        """拒绝无法形成单调比较的 MAE。."""
        _validate_mae(self.mae)


@dataclass(frozen=True)
class _ParameterBinding:
    parameter: TunableParameter
    segments: tuple[str | int, ...]
    feature_id: str | None = None


def _parameter(
    path: str,
    segments: tuple[str | int, ...],
    minimum: float,
    maximum: float,
    step: float,
    *,
    feature_id: str | None = None,
) -> _ParameterBinding:
    return _ParameterBinding(
        TunableParameter(path, minimum, maximum, step),
        segments,
        feature_id,
    )


def _base_bindings(scene: MinScene) -> tuple[_ParameterBinding, ...]:
    unit = float(min(scene.canvas.width, scene.canvas.height))
    extent_x = scene.canvas.width / unit
    extent_y = scene.canvas.height / unit
    primitive = ("object", "primitive")
    field = ("object", "color_field")
    return (
        _parameter(
            "object.primitive.center[0]",
            (*primitive, "center", 0),
            -extent_x,
            extent_x,
            0.03,
        ),
        _parameter(
            "object.primitive.center[1]",
            (*primitive, "center", 1),
            -extent_y,
            extent_y,
            0.03,
        ),
        _parameter(
            "object.primitive.axes[0]", (*primitive, "axes", 0), 0.02, extent_x, 0.04
        ),
        _parameter(
            "object.primitive.axes[1]", (*primitive, "axes", 1), 0.02, extent_y, 0.04
        ),
        _parameter("canvas.background[0]", ("canvas", "background", 0), 0.0, 1.0, 0.04),
        _parameter("canvas.background[1]", ("canvas", "background", 1), 0.0, 1.0, 0.04),
        _parameter("canvas.background[2]", ("canvas", "background", 2), 0.0, 1.0, 0.04),
        _parameter("object.color_field.inner[0]", (*field, "inner", 0), 0.0, 1.0, 0.04),
        _parameter("object.color_field.inner[1]", (*field, "inner", 1), 0.0, 1.0, 0.04),
        _parameter("object.color_field.inner[2]", (*field, "inner", 2), 0.0, 1.0, 0.04),
        _parameter("object.color_field.outer[0]", (*field, "outer", 0), 0.0, 1.0, 0.04),
        _parameter("object.color_field.outer[1]", (*field, "outer", 1), 0.0, 1.0, 0.04),
        _parameter("object.color_field.outer[2]", (*field, "outer", 2), 0.0, 1.0, 0.04),
        _parameter(
            "object.color_field.origin[0]", (*field, "origin", 0), -2.0, 2.0, 0.05
        ),
        _parameter(
            "object.color_field.origin[1]", (*field, "origin", 1), -2.0, 2.0, 0.05
        ),
        _parameter("object.color_field.scale", (*field, "scale"), 0.050001, 4.0, 0.08),
    )


def _feature_bindings(
    scene: MinScene, feature_id: str
) -> tuple[_ParameterBinding, ...]:
    feature_index = next(
        (
            index
            for index, item in enumerate(scene.object.features)
            if item.id == feature_id
        ),
        None,
    )
    if feature_index is None:
        raise ValueError(f"scene 中不存在 feature_id={feature_id!r}。")
    unit = float(min(scene.canvas.width, scene.canvas.height))
    extent_x = scene.canvas.width / unit
    extent_y = scene.canvas.height / unit
    prefix = ("object", "features", feature_index)
    label = f"object.features[id={feature_id!r}]"
    return (
        _parameter(
            f"{label}.center[0]",
            (*prefix, "center", 0),
            -extent_x,
            extent_x,
            0.03,
            feature_id=feature_id,
        ),
        _parameter(
            f"{label}.center[1]",
            (*prefix, "center", 1),
            -extent_y,
            extent_y,
            0.03,
            feature_id=feature_id,
        ),
        _parameter(
            f"{label}.axes[0]",
            (*prefix, "axes", 0),
            0.02,
            extent_x,
            0.03,
            feature_id=feature_id,
        ),
        _parameter(
            f"{label}.axes[1]",
            (*prefix, "axes", 1),
            0.02,
            extent_y,
            0.03,
            feature_id=feature_id,
        ),
        _parameter(
            f"{label}.color[0]",
            (*prefix, "color", 0),
            0.0,
            1.0,
            0.04,
            feature_id=feature_id,
        ),
        _parameter(
            f"{label}.color[1]",
            (*prefix, "color", 1),
            0.0,
            1.0,
            0.04,
            feature_id=feature_id,
        ),
        _parameter(
            f"{label}.color[2]",
            (*prefix, "color", 2),
            0.0,
            1.0,
            0.04,
            feature_id=feature_id,
        ),
        _parameter(
            f"{label}.intensity",
            (*prefix, "intensity"),
            0.0,
            2.0,
            0.08,
            feature_id=feature_id,
        ),
    )


def _read_path(value: Any, segments: tuple[str | int, ...]) -> float:
    current = value
    for segment in segments:
        current = current[segment]
    result = float(current)
    if not math.isfinite(result):
        raise ValueError("优化参数必须是有限数值。")
    return result


def _replace_path(
    value: Any, segments: tuple[str | int, ...], replacement: float
) -> Any:
    head, *tail = segments
    if isinstance(head, str):
        copied = dict(value)
        copied[head] = (
            _replace_path(copied[head], tuple(tail), replacement)
            if tail
            else replacement
        )
        return copied
    copied_items = list(value)
    copied_items[head] = (
        _replace_path(copied_items[head], tuple(tail), replacement)
        if tail
        else replacement
    )
    return tuple(copied_items)


def _candidate_limit(
    stage: OptimizationStage,
    remaining_draw_budget: int,
    batch_size: int | None,
) -> int:
    if (
        not isinstance(remaining_draw_budget, int)
        or isinstance(remaining_draw_budget, bool)
        or remaining_draw_budget < 0
    ):
        raise ValueError("remaining_draw_budget 必须是非负整数。")
    if batch_size is not None and (
        not isinstance(batch_size, int)
        or isinstance(batch_size, bool)
        or batch_size <= 0
    ):
        raise ValueError("batch_size 必须是正整数。")
    default = (
        _DEFAULT_BASE_BATCH_SIZE if stage == "base" else _DEFAULT_FEATURE_BATCH_SIZE
    )
    requested = default if batch_size is None else batch_size
    return min(remaining_draw_budget, requested, MAX_CANDIDATES_PER_BATCH)


def propose_min_scene_candidates(
    scene: MinScene,
    *,
    stage: OptimizationStage,
    remaining_draw_budget: int,
    feature_id: str | None = None,
    batch_size: int | None = None,
) -> tuple[CandidateProposal, ...]:
    """按固定顺序返回被 draw 预算截断的单参数合法候选。."""
    if stage not in ("base", "feature"):
        raise ValueError("stage 必须是 base 或 feature。")
    if stage == "base":
        if feature_id is not None:
            raise ValueError("base 阶段不接受 feature_id。")
        bindings = _base_bindings(scene)
    else:
        if feature_id is None:
            raise ValueError("feature 阶段必须指定稳定 feature_id。")
        bindings = _feature_bindings(scene, feature_id)

    limit = _candidate_limit(stage, remaining_draw_budget, batch_size)
    if limit == 0:
        return ()
    source = scene.model_dump(mode="python")
    proposals: list[CandidateProposal] = []
    seen_scenes: set[str] = set()
    directions: tuple[tuple[Literal["decrease", "increase"], float], ...] = (
        ("decrease", -1.0),
        ("increase", 1.0),
    )
    # 先让每个字段获得一个邻居，再补相反方向，避免小批预算长期饿死尾部字段。
    for direction, sign in directions:
        for binding in bindings:
            before = _read_path(source, binding.segments)
            signed_step = sign * binding.parameter.step
            after = min(
                binding.parameter.maximum,
                max(binding.parameter.minimum, before + signed_step),
            )
            after = round(after, 9)
            if after == before:
                continue
            candidate = MinScene.model_validate(
                _replace_path(source, binding.segments, after)
            )
            fingerprint = candidate.model_dump_json()
            if fingerprint in seen_scenes:
                continue
            seen_scenes.add(fingerprint)
            proposals.append(
                CandidateProposal(
                    stage=stage,
                    parameter=binding.parameter,
                    direction=direction,
                    before=before,
                    after=after,
                    scene=candidate,
                    feature_id=binding.feature_id,
                )
            )
            if len(proposals) == limit:
                return tuple(proposals)
    return tuple(proposals)


def rebase_candidate_proposal(
    scene: MinScene,
    proposal: CandidateProposal,
) -> CandidateProposal | None:
    """把固定候选计划重放到最新 best，使同批已接受变化可以累积。."""
    bindings = (
        _base_bindings(scene)
        if proposal.stage == "base"
        else _feature_bindings(scene, proposal.feature_id or "")
    )
    binding = next(
        (
            item
            for item in bindings
            if item.parameter.path == proposal.parameter.path
        ),
        None,
    )
    if binding is None:
        raise ValueError(f"候选参数已不属于当前 scene：{proposal.parameter.path}。")
    source = scene.model_dump(mode="python")
    before = _read_path(source, binding.segments)
    sign = -1.0 if proposal.direction == "decrease" else 1.0
    after = min(
        binding.parameter.maximum,
        max(binding.parameter.minimum, before + sign * binding.parameter.step),
    )
    after = round(after, 9)
    if after == before:
        return None
    return CandidateProposal(
        stage=proposal.stage,
        parameter=binding.parameter,
        direction=proposal.direction,
        before=before,
        after=after,
        scene=MinScene.model_validate(_replace_path(source, binding.segments, after)),
        feature_id=proposal.feature_id,
    )


def accept_strict_mae_improvement(
    current: ScoredScene,
    proposal: CandidateProposal,
    candidate_mae: float,
) -> ScoredScene:
    """只在候选 MAE 严格更小时返回新的已接受快照。."""
    _validate_mae(candidate_mae)
    if candidate_mae < current.mae:
        return ScoredScene(scene=proposal.scene, mae=candidate_mae)
    return current


def _validate_mae(value: float) -> None:
    if not math.isfinite(value) or value < 0.0:
        raise ValueError("MAE 必须是有限非负数。")


__all__ = [
    "MAX_CANDIDATES_PER_BATCH",
    "CandidateProposal",
    "OptimizationStage",
    "ScoredScene",
    "TunableParameter",
    "accept_strict_mae_improvement",
    "propose_min_scene_candidates",
    "rebase_candidate_proposal",
]
