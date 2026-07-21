"""PNG 转无贴图 Shader V1 的稳定运行契约."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any


class ProblemDomain(str, Enum):
    """单轮 Shader 修订允许选择的问题域."""

    RUNTIME_COMPILE = "runtime_compile"
    GEOMETRY = "geometry"
    BACKGROUND_SHADOW = "background_shadow"
    BASE_COLOR_FIELD = "base_color_field"
    RIM_EDGE = "rim_edge"
    HIGHLIGHT = "highlight"
    FINE_DETAIL = "fine_detail"
    GLOBAL_BALANCE = "global_balance"


class StopReason(str, Enum):
    """V1 有界闭环的停止原因."""

    QUALITY_THRESHOLD_MET = "quality_threshold_met"
    STAGNATION = "stagnation"
    VISUAL_ITERATION_BUDGET_EXHAUSTED = "visual_iteration_budget_exhausted"
    MODEL_BUDGET_EXHAUSTED = "model_budget_exhausted"
    WALL_TIME_EXHAUSTED = "wall_time_exhausted"
    COMPILE_REPAIR_EXHAUSTED = "compile_repair_exhausted"
    RENDERER_UNAVAILABLE = "renderer_unavailable"
    CANCELLED = "cancelled"
    COMPLETED_WITH_BEST_EFFORT = "completed_with_best_effort"


class QualityPreset(str, Enum):
    """用户可选择的 V1 质量与成本档位."""

    FAST = "fast"
    BALANCED = "balanced"
    HIGH = "high"
    ULTRA = "ultra"


@dataclass(frozen=True)
class RenderContract:
    """WebGL1 无贴图 Fragment Shader 运行契约."""

    contract_id: str
    glsl_version: str
    precision: str
    varying_name: str
    required_uniforms: tuple[tuple[str, str], ...]
    fragment_output: str
    uv_origin: str
    texture_sampling_allowed: bool
    animation_enabled: bool
    max_long_side: int
    required_declarations: tuple[str, ...]
    forbidden_tokens: tuple[str, ...]

    def __post_init__(self) -> None:
        """拒绝无法安全执行的契约定义."""
        if not self.contract_id.strip():
            raise ValueError("contract_id 不能为空。")
        if self.max_long_side <= 0:
            raise ValueError("max_long_side 必须大于 0。")
        if self.uv_origin not in {"bottom_left", "top_left"}:
            raise ValueError("uv_origin 只能是 bottom_left 或 top_left。")
        if len({name for name, _ in self.required_uniforms}) != len(
            self.required_uniforms
        ):
            raise ValueError("required_uniforms 不能包含重复名称。")

    def to_dict(self) -> dict[str, Any]:
        """返回适合 Prompt、日志和 manifest 的普通字典."""
        return asdict(self)


@dataclass(frozen=True)
class BudgetPolicy:
    """单次 V1 运行的硬预算."""

    max_visual_refinements: int
    max_compile_repairs: int
    max_model_calls: int
    max_wall_time_seconds: int
    max_shader_chars: int = 30_000
    renderer_replay_on_crash: int = 1

    def __post_init__(self) -> None:
        """保证预算有界且数值可执行."""
        positive_fields = (
            self.max_model_calls,
            self.max_wall_time_seconds,
            self.max_shader_chars,
        )
        if any(value <= 0 for value in positive_fields):
            raise ValueError("模型、时间和源码预算必须大于 0。")
        non_negative_fields = (
            self.max_visual_refinements,
            self.max_compile_repairs,
            self.renderer_replay_on_crash,
        )
        if any(value < 0 for value in non_negative_fields):
            raise ValueError("迭代、修复和重放预算不能小于 0。")


@dataclass(frozen=True)
class AcceptancePolicy:
    """候选替换 current_best 的确定性门槛."""

    min_total_improvement: float = 0.005
    max_protected_regression: float = 0.02
    quality_threshold: float = 0.12
    stagnation_rounds: int = 2

    def __post_init__(self) -> None:
        """校验评分阈值的基本范围."""
        for name, value in (
            ("min_total_improvement", self.min_total_improvement),
            ("max_protected_regression", self.max_protected_regression),
            ("quality_threshold", self.quality_threshold),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} 必须位于 0.0 到 1.0。")
        if self.stagnation_rounds <= 0:
            raise ValueError("stagnation_rounds 必须大于 0。")


WEBGL1_STATIC_NO_TEXTURE_V1 = RenderContract(
    contract_id="webgl1_static_no_texture_v1",
    glsl_version="GLSL_ES_100",
    precision="mediump",
    varying_name="v_uv",
    required_uniforms=(
        ("u_image", "sampler2D"),
        ("u_resolution", "vec2"),
        ("u_time", "float"),
    ),
    fragment_output="gl_FragColor",
    uv_origin="bottom_left",
    texture_sampling_allowed=False,
    animation_enabled=False,
    max_long_side=1024,
    required_declarations=(
        "precision mediump float;",
        "varying vec2 v_uv;",
        "uniform sampler2D u_image;",
        "uniform vec2 u_resolution;",
        "uniform float u_time;",
        "void main()",
    ),
    forbidden_tokens=(
        "#version",
        "texture2D",
        "textureCube",
        "texture(",
        "texelFetch",
        "mainImage",
    ),
)


QUALITY_PRESETS = MappingProxyType(
    {
        QualityPreset.FAST: BudgetPolicy(
            max_visual_refinements=1,
            max_compile_repairs=1,
            max_model_calls=5,
            max_wall_time_seconds=180,
        ),
        QualityPreset.BALANCED: BudgetPolicy(
            max_visual_refinements=2,
            max_compile_repairs=2,
            max_model_calls=8,
            max_wall_time_seconds=300,
        ),
        QualityPreset.HIGH: BudgetPolicy(
            max_visual_refinements=4,
            max_compile_repairs=2,
            max_model_calls=12,
            max_wall_time_seconds=600,
        ),
        QualityPreset.ULTRA: BudgetPolicy(
            max_visual_refinements=10,
            max_compile_repairs=5,
            max_model_calls=40,
            max_wall_time_seconds=2_400,
            renderer_replay_on_crash=2,
        ),
    }
)

DEFAULT_ACCEPTANCE_POLICY = AcceptancePolicy()
PROBLEM_DOMAINS = tuple(domain.value for domain in ProblemDomain)
STOP_REASONS = tuple(reason.value for reason in StopReason)


def budget_for_preset(preset: QualityPreset | str) -> BudgetPolicy:
    """按质量档位返回不可变的预算策略."""
    try:
        normalized = (
            preset if isinstance(preset, QualityPreset) else QualityPreset(preset)
        )
    except ValueError as exc:
        allowed = ", ".join(item.value for item in QualityPreset)
        raise ValueError(f"quality preset 必须是 {allowed}。") from exc
    return QUALITY_PRESETS[normalized]
