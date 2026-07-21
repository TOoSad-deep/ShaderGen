"""PNG-to-Shader V1 运行预算与验收策略的严格 YAML 边界."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from yaml.nodes import MappingNode

from shaderforge.contracts import (
    AcceptancePolicy,
    BudgetPolicy,
    QualityPreset,
    budget_for_preset,
)

RUNTIME_POLICY_SCHEMA_VERSION = "png_to_shader_runtime_policy_v2"
DEFAULT_RUNTIME_POLICY_PATH = str(
    Path(__file__).with_name("png_to_shader_runtime_policy.v2.yaml")
)
MAX_RUNTIME_POLICY_BYTES = 64 * 1024
_REQUIRED_PROFILES = frozenset(item.value for item in QualityPreset)


class RuntimePolicyConfigurationError(ValueError):
    """表示运行策略文件缺失、格式错误或突破硬上限."""


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """仅允许安全 YAML，并拒绝任意层级的重复 key."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    if not isinstance(node, MappingNode):  # pragma: no cover - PyYAML 内部保证
        raise RuntimePolicyConfigurationError("YAML mapping 节点无效。")
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicated = key in result
        except TypeError as exc:
            raise RuntimePolicyConfigurationError("YAML key 必须可比较。") from exc
        if duplicated:
            raise RuntimePolicyConfigurationError(f"YAML 字段不得重复：{key}。")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


class _StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _BudgetConfig(_StrictConfigModel):
    max_visual_refinements: int = Field(ge=0)
    max_compile_repairs: int = Field(ge=0)
    max_model_calls: int = Field(gt=0)
    max_wall_time_seconds: int = Field(gt=0)
    max_shader_chars: int = Field(gt=0)
    renderer_replay_on_crash: int = Field(ge=0)

    def to_domain(self) -> BudgetPolicy:
        """转换为领域 BudgetPolicy."""
        return BudgetPolicy(**self.model_dump(mode="python"))


class _AcceptanceConfig(_StrictConfigModel):
    min_total_improvement: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    max_protected_regression: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    quality_threshold: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    stagnation_rounds: int = Field(gt=0)

    def to_domain(self) -> AcceptancePolicy:
        """转换为领域 AcceptancePolicy."""
        return AcceptancePolicy(**self.model_dump(mode="python"))


class _ProfileConfig(_StrictConfigModel):
    budget: _BudgetConfig
    acceptance: _AcceptanceConfig


class _RuntimePolicyDocument(_StrictConfigModel):
    schema_version: Literal["png_to_shader_runtime_policy_v2"]
    profiles: dict[str, _ProfileConfig]

    @model_validator(mode="after")
    def _require_frozen_profiles(self) -> _RuntimePolicyDocument:
        actual = frozenset(self.profiles)
        if actual != _REQUIRED_PROFILES:
            missing = ", ".join(sorted(_REQUIRED_PROFILES - actual)) or "none"
            unknown = ", ".join(sorted(actual - _REQUIRED_PROFILES)) or "none"
            raise ValueError(
                "profiles 必须恰好包含 fast、balanced、high、ultra；"
                f"missing={missing} unknown={unknown}。"
            )
        return self


@dataclass(frozen=True, slots=True)
class ResolvedRuntimePolicy:
    """单个 quality preset 解析后的不可变运行策略."""

    profile: str
    budget: BudgetPolicy
    acceptance: AcceptancePolicy
    config_schema_version: str
    config_sha256: str

    def evidence(self) -> dict[str, Any]:
        """返回可写入 State、Manifest 与数据库的路径无关证据."""
        return {
            "schema_version": self.config_schema_version,
            "config_sha256": self.config_sha256,
            "profile": self.profile,
            "budget": asdict(self.budget),
            "acceptance": asdict(self.acceptance),
        }


@dataclass(frozen=True, slots=True)
class RuntimePolicyRegistry:
    """一次 Backend 生命周期内冻结的 profile registry."""

    schema_version: str
    config_sha256: str
    source_path: Path
    profiles: MappingProxyType[str, ResolvedRuntimePolicy]

    def resolve(self, preset: QualityPreset | str) -> ResolvedRuntimePolicy:
        """按公开四档 API 名称解析策略."""
        try:
            name = (
                preset.value
                if isinstance(preset, QualityPreset)
                else QualityPreset(preset).value
            )
        except ValueError as exc:
            raise RuntimePolicyConfigurationError(f"未知运行策略 profile：{preset}。") from exc
        return self.profiles[name]


def _assert_within_hard_ceiling(profile: str, budget: BudgetPolicy) -> None:
    ceiling = budget_for_preset(QualityPreset.ULTRA)
    for field_name in (
        "max_visual_refinements",
        "max_compile_repairs",
        "max_model_calls",
        "max_wall_time_seconds",
        "max_shader_chars",
        "renderer_replay_on_crash",
    ):
        actual = getattr(budget, field_name)
        maximum = getattr(ceiling, field_name)
        if actual > maximum:
            raise RuntimePolicyConfigurationError(
                f"profile {profile} 的 {field_name}={actual} "
                f"超过代码硬上限 {maximum}。"
            )


def load_runtime_policy(path: str | Path) -> RuntimePolicyRegistry:
    """读取、严格校验并冻结一个运行策略 YAML."""
    source_path = Path(path).expanduser().resolve()
    try:
        raw = source_path.read_bytes()
    except OSError as exc:
        raise RuntimePolicyConfigurationError(
            f"无法读取运行策略文件：{source_path}。"
        ) from exc
    if not raw:
        raise RuntimePolicyConfigurationError("运行策略文件不能为空。")
    if len(raw) > MAX_RUNTIME_POLICY_BYTES:
        raise RuntimePolicyConfigurationError(
            f"运行策略文件不能超过 {MAX_RUNTIME_POLICY_BYTES} bytes。"
        )
    try:
        text = raw.decode("utf-8")
        payload = yaml.load(text, Loader=_UniqueKeySafeLoader)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise RuntimePolicyConfigurationError("运行策略文件不是严格 UTF-8 YAML。") from exc
    try:
        document = _RuntimePolicyDocument.model_validate(payload, strict=True)
    except Exception as exc:
        raise RuntimePolicyConfigurationError("运行策略 Schema 校验失败。") from exc

    digest = sha256(raw).hexdigest()
    profiles: dict[str, ResolvedRuntimePolicy] = {}
    for profile_name, profile_config in document.profiles.items():
        budget = profile_config.budget.to_domain()
        _assert_within_hard_ceiling(profile_name, budget)
        profiles[profile_name] = ResolvedRuntimePolicy(
            profile=profile_name,
            budget=budget,
            acceptance=profile_config.acceptance.to_domain(),
            config_schema_version=document.schema_version,
            config_sha256=digest,
        )
    return RuntimePolicyRegistry(
        schema_version=document.schema_version,
        config_sha256=digest,
        source_path=source_path,
        profiles=MappingProxyType(profiles),
    )
