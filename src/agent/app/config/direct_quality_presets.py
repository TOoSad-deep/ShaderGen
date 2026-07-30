"""Load and validate Direct quality presets from YAML."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

_DEFAULT_CONFIG_RESOURCE = "direct_quality_presets.yaml"
_QUALITY_PRESETS = frozenset({"fast", "balanced", "high", "manual"})


class _OptimizationPolicyModel(BaseModel):
    """Strict YAML model for one preset's convergence policy."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    target_mae: float = Field(ge=0, le=1, allow_inf_nan=False)
    target_loss: float = Field(ge=0, le=1, allow_inf_nan=False)
    min_delta_mae: float = Field(ge=0, le=1, allow_inf_nan=False)
    min_delta_loss: float = Field(ge=0, le=1, allow_inf_nan=False)
    refinement_patience: int = Field(ge=0)
    detect_duplicate_patch: bool

    @model_validator(mode="before")
    @classmethod
    def reject_implicit_scalar_types(cls, value: Any) -> Any:
        """Reject bools and YAML strings where numeric values are required."""
        if not isinstance(value, dict):
            return value
        for name in (
            "target_mae",
            "target_loss",
            "min_delta_mae",
            "min_delta_loss",
        ):
            item = value.get(name)
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise ValueError(f"{name} must be a finite number")
        patience = value.get("refinement_patience")
        if isinstance(patience, bool) or not isinstance(patience, int):
            raise ValueError("refinement_patience must be an integer")
        return value


class _BudgetsModel(BaseModel):
    """Strict YAML model for one preset's attempt-wide budgets."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    direct_author_llm_budget: int = Field(ge=0)
    compile_budget: int = Field(ge=0)
    draw_budget: int = Field(ge=0)
    refine_budget: int = Field(ge=0)
    plan_llm_budget: int = Field(ge=0)
    uniform_tuning_draw_budget: int = Field(ge=0)
    uniform_tuning_active_component_cap: int = Field(ge=0)
    uniform_tuning_max_passes: int = Field(ge=0)

    @model_validator(mode="before")
    @classmethod
    def reject_non_integer_budgets(cls, value: Any) -> Any:
        """Reject bools, floats, and YAML strings for discrete budgets."""
        if not isinstance(value, dict):
            return value
        for name, item in value.items():
            if isinstance(item, bool) or not isinstance(item, int):
                raise ValueError(f"{name} must be an integer")
        return value

    @model_validator(mode="after")
    def validate_attempt_capacity(self) -> _BudgetsModel:
        """Ensure declared Refine and tuning work fits inside hard budgets."""
        structural_candidates = 1 + self.refine_budget
        if self.direct_author_llm_budget < structural_candidates:
            raise ValueError(
                "direct_author_llm_budget must cover Initial plus all Refine rounds"
            )
        if self.compile_budget < structural_candidates:
            raise ValueError("compile_budget must cover Initial plus all Refine rounds")
        required_draws = structural_candidates + self.uniform_tuning_draw_budget
        if self.draw_budget < required_draws:
            raise ValueError(
                "draw_budget must cover structural and uniform-tuning draws"
            )
        if self.plan_llm_budget < 1:
            raise ValueError("plan_llm_budget must allow LayerPlan authoring")
        return self


class _PresetModel(BaseModel):
    """Strict YAML model for one named quality preset."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    optimization_policy: _OptimizationPolicyModel
    budgets: _BudgetsModel


class _RootModel(BaseModel):
    """Strict root YAML schema."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal["direct_quality_presets_v1"]
    presets: dict[str, _PresetModel]

    @model_validator(mode="after")
    def validate_preset_names(self) -> _RootModel:
        """Require exactly the four public quality presets."""
        actual = frozenset(self.presets)
        if actual != _QUALITY_PRESETS:
            missing = sorted(_QUALITY_PRESETS - actual)
            extra = sorted(actual - _QUALITY_PRESETS)
            raise ValueError(
                "presets must contain exactly fast/balanced/high/manual; "
                f"missing={missing} extra={extra}"
            )
        return self


@dataclass(frozen=True, slots=True)
class DirectOptimizationPreset:
    """Validated convergence settings for one quality preset."""

    target_mae: float
    target_loss: float
    min_delta_mae: float
    min_delta_loss: float
    refinement_patience: int
    detect_duplicate_patch: bool


@dataclass(frozen=True, slots=True)
class DirectBudgetPreset:
    """Validated attempt budgets for one quality preset."""

    direct_author_llm_budget: int
    compile_budget: int
    draw_budget: int
    refine_budget: int
    plan_llm_budget: int
    uniform_tuning_draw_budget: int
    uniform_tuning_active_component_cap: int
    uniform_tuning_max_passes: int


@dataclass(frozen=True, slots=True)
class DirectQualityPreset:
    """Validated policy and budgets for one quality preset."""

    optimization_policy: DirectOptimizationPreset
    budgets: DirectBudgetPreset


@dataclass(frozen=True, slots=True)
class DirectQualityPresets:
    """Immutable collection of all public Direct quality presets."""

    version: str
    presets: Mapping[str, DirectQualityPreset]

    def for_quality_preset(self, quality_preset: str) -> DirectQualityPreset:
        """Return one named preset or reject an unsupported public value."""
        try:
            return self.presets[quality_preset]
        except KeyError as exc:
            raise ValueError("quality_preset is unsupported") from exc


def _read_config(path: Path | None) -> Any:
    """Read an explicit YAML file or the wheel-packaged default resource."""
    if path is not None:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    resource = files("agent.app.config").joinpath(_DEFAULT_CONFIG_RESOURCE)
    return yaml.safe_load(resource.read_text(encoding="utf-8"))


def load_direct_quality_presets(
    path: str | Path | None = None,
) -> DirectQualityPresets:
    """Load Direct quality presets and fail on unknown or incoherent values."""
    resolved_path = Path(path) if path is not None else None
    try:
        parsed = _RootModel.model_validate(_read_config(resolved_path))
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError) as exc:
        source = str(resolved_path) if resolved_path else _DEFAULT_CONFIG_RESOURCE
        raise ValueError(
            f"Direct quality preset config is invalid: {source}: {exc}"
        ) from exc

    presets: dict[str, DirectQualityPreset] = {}
    for name, preset in parsed.presets.items():
        presets[name] = DirectQualityPreset(
            optimization_policy=DirectOptimizationPreset(
                **preset.optimization_policy.model_dump()
            ),
            budgets=DirectBudgetPreset(**preset.budgets.model_dump()),
        )
    return DirectQualityPresets(
        version=parsed.version,
        presets=MappingProxyType(presets),
    )


DIRECT_QUALITY_PRESETS = load_direct_quality_presets()

__all__ = [
    "DIRECT_QUALITY_PRESETS",
    "DirectBudgetPreset",
    "DirectOptimizationPreset",
    "DirectQualityPreset",
    "DirectQualityPresets",
    "load_direct_quality_presets",
]
