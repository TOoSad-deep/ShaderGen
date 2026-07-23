"""加载并校验 scene_mvp 的目标与运行预算."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from shaderforge.generation import MAX_MIN_FEATURES

_FROZEN_QUALITY_PRESET_NAMES = frozenset({"fast", "balanced", "high"})
_INDEPENDENT_QUALITY_PRESET_NAMES = frozenset(
    {"fast", "balanced", "high", "manual"}
)
_DEFAULT_CONFIG_RESOURCE = "png_to_shader_min.yaml"
_FROZEN_TARGETS = (0.08, 0.04)
_FROZEN_QUALITY_PRESETS = {
    "fast": (48, 2, 1),
    "balanced": (96, 4, 2),
    "high": (160, 6, 3),
}
MIN_GRAPH_RECURSION_SAFETY_MARGIN = 4
MAX_MIN_GRAPH_RECURSION_LIMIT = 256


def _non_negative_int(value: int, name: str) -> int:
    """拒绝会让 Graph 上界推导失真的布尔值或负整数."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} 必须是非负整数。")
    return value


def max_min_refine_iterations(llm_budget: int, refine_budget: int) -> int:
    """返回 Initial Author 消耗一次模型预算后的最大 Refine 轮数."""
    llm = _non_negative_int(llm_budget, "llm_budget")
    refine = _non_negative_int(refine_budget, "refine_budget")
    return min(refine, max(0, llm - 1))


def required_min_graph_steps(
    llm_budget: int,
    refine_budget: int,
    *,
    max_features: int = MAX_MIN_FEATURES,
) -> int:
    """按当前固定拓扑推导一次合法 run 的最坏节点步数.

    固定前缀、首次 base/feature sweep 与 finalize 共 ``9 + 2F`` 步。
    每轮 Refine 在 render 节点内完成 Patch 局部成熟，随后经 no-op base
    过桥到决定节点，共 6 步；不会重新遍历全部 feature。
    """
    features = _non_negative_int(max_features, "max_features")
    if features > MAX_MIN_FEATURES:
        raise ValueError(f"max_features 不得超过固定槽位 {MAX_MIN_FEATURES}。")
    refinements = max_min_refine_iterations(llm_budget, refine_budget)
    return 9 + 2 * features + refinements * 6


def derive_min_graph_recursion_limit(
    llm_budget: int,
    refine_budget: int,
    *,
    max_features: int = MAX_MIN_FEATURES,
) -> int:
    """为合法预算路径派生带小幅框架余量的 run 级递归上限."""
    required = required_min_graph_steps(
        llm_budget,
        refine_budget,
        max_features=max_features,
    )
    derived = required + MIN_GRAPH_RECURSION_SAFETY_MARGIN
    if derived > MAX_MIN_GRAPH_RECURSION_LIMIT:
        raise ValueError(
            "scene_mvp 预算推导的 Graph recursion limit "
            f"{derived} 超过安全上限 {MAX_MIN_GRAPH_RECURSION_LIMIT}。"
        )
    return derived


class _TargetsConfig(BaseModel):
    """YAML 内的质量目标."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    mae: float = Field(ge=0.0, le=1.0)
    loss: float = Field(ge=0.0, le=1.0)


class _BudgetConfig(BaseModel):
    """YAML 内的单档运行预算."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    render_budget: int = Field(ge=1)
    llm_budget: int = Field(ge=0)
    refine_budget: int = Field(ge=0)


class _RootConfig(BaseModel):
    """YAML 根结构."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: str = Field(min_length=1, max_length=100)
    run_classification: Literal["frozen_benchmark", "independent_experiment"]
    experiment_id: str | None = Field(default=None, min_length=1, max_length=100)
    report_schema_version: str = Field(min_length=1, max_length=100)
    targets: _TargetsConfig
    quality_presets: dict[str, _BudgetConfig]

    @model_validator(mode="after")
    def validate_quality_presets(self) -> _RootConfig:
        """校验公开档位，并禁止漂移配置冒充冻结 benchmark."""
        expected = (
            _INDEPENDENT_QUALITY_PRESET_NAMES
            if self.run_classification == "independent_experiment"
            else _FROZEN_QUALITY_PRESET_NAMES
        )
        actual = frozenset(self.quality_presets)
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise ValueError(
                f"{self.run_classification} quality_presets 必须恰好包含"
                f" {sorted(expected)}；"
                f"missing={missing} extra={extra}"
            )
        for name, budget in self.quality_presets.items():
            try:
                derive_min_graph_recursion_limit(
                    budget.llm_budget,
                    budget.refine_budget,
                )
            except ValueError as exc:
                raise ValueError(f"quality_presets.{name} 无法安全执行：{exc}") from exc
        if self.run_classification == "independent_experiment":
            if self.experiment_id is None:
                raise ValueError("independent_experiment 必须提供独立 experiment_id。")
            return self
        if self.experiment_id is not None:
            raise ValueError("frozen_benchmark 不得设置 experiment_id。")
        actual_targets = (self.targets.mae, self.targets.loss)
        if actual_targets != _FROZEN_TARGETS:
            raise ValueError(
                "frozen_benchmark targets 必须严格等于 "
                f"mae/loss={_FROZEN_TARGETS}，实际为 {actual_targets}。"
            )
        actual_presets = {
            name: (
                budget.render_budget,
                budget.llm_budget,
                budget.refine_budget,
            )
            for name, budget in self.quality_presets.items()
        }
        if actual_presets != _FROZEN_QUALITY_PRESETS:
            raise ValueError(
                "frozen_benchmark quality_presets 必须严格等于 D058/D059 "
                f"冻结预算 {_FROZEN_QUALITY_PRESETS}。"
            )
        return self


@dataclass(frozen=True)
class MinQualityBudget:
    """scene_mvp 单个质量档位的目标与硬预算."""

    render_budget: int
    llm_budget: int
    refine_budget: int
    target_mae: float
    target_loss: float
    recursion_limit: int


@dataclass(frozen=True)
class MinPipelineConfig:
    """完成校验且不可变的 scene_mvp 运行策略."""

    version: str
    run_classification: Literal["frozen_benchmark", "independent_experiment"]
    experiment_id: str | None
    report_schema_version: str
    config_fingerprint: str
    quality_presets: MappingProxyType[str, MinQualityBudget]

    @property
    def max_llm_budget(self) -> int:
        """返回配置中的最大模型调用预算."""
        return max(item.llm_budget for item in self.quality_presets.values())

    @property
    def max_refine_budget(self) -> int:
        """返回配置中的最大 Refine 预算."""
        return max(item.refine_budget for item in self.quality_presets.values())

    @property
    def max_recursion_limit(self) -> int:
        """返回全部合法路径所需的最大 run 级递归上限."""
        return max(item.recursion_limit for item in self.quality_presets.values())


def _read_config(path: Path | None) -> Any:
    """读取显式文件或包内默认 YAML."""
    if path is not None:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    resource = files("agent.app.config").joinpath(_DEFAULT_CONFIG_RESOURCE)
    return yaml.safe_load(resource.read_text(encoding="utf-8"))


def _config_fingerprint(config: _RootConfig) -> str:
    """对已校验的实际配置生成与 YAML 排版无关的稳定指纹."""
    canonical = json.dumps(
        config.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def load_min_pipeline_config(path: str | Path | None = None) -> MinPipelineConfig:
    """加载 scene_mvp YAML，并对未知字段、类型和值域执行 fail-fast 校验."""
    resolved_path = Path(path) if path is not None else None
    try:
        parsed = _RootConfig.model_validate(_read_config(resolved_path))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        source = (
            str(resolved_path)
            if resolved_path is not None
            else _DEFAULT_CONFIG_RESOURCE
        )
        raise ValueError(f"scene_mvp 配置无效：{source}: {exc}") from exc

    quality_presets = MappingProxyType(
        {
            name: MinQualityBudget(
                render_budget=budget.render_budget,
                llm_budget=budget.llm_budget,
                refine_budget=budget.refine_budget,
                target_mae=parsed.targets.mae,
                target_loss=parsed.targets.loss,
                recursion_limit=derive_min_graph_recursion_limit(
                    budget.llm_budget,
                    budget.refine_budget,
                ),
            )
            for name, budget in parsed.quality_presets.items()
        }
    )
    return MinPipelineConfig(
        version=parsed.version,
        run_classification=parsed.run_classification,
        experiment_id=parsed.experiment_id,
        report_schema_version=parsed.report_schema_version,
        config_fingerprint=_config_fingerprint(parsed),
        quality_presets=quality_presets,
    )


MIN_PIPELINE_CONFIG = load_min_pipeline_config()


__all__ = [
    "MAX_MIN_GRAPH_RECURSION_LIMIT",
    "MIN_PIPELINE_CONFIG",
    "MIN_GRAPH_RECURSION_SAFETY_MARGIN",
    "MinPipelineConfig",
    "MinQualityBudget",
    "derive_min_graph_recursion_limit",
    "load_min_pipeline_config",
    "max_min_refine_iterations",
    "required_min_graph_steps",
]
