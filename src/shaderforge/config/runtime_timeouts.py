"""跨 Backend、Agent、Renderer 与 Frontend 的统一 timeout YAML 契约."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

_DEFAULT_CONFIG_RESOURCE = "runtime_timeouts.yaml"
_QUALITY_PRESETS = frozenset({"fast", "balanced", "high", "manual"})


class _SecondsModel(BaseModel):
    """只接受正有限秒数的严格配置基类."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="before")
    @classmethod
    def reject_non_numeric_seconds(cls, value: Any) -> Any:
        """拒绝 bool、字符串和其他 YAML 隐式类型."""
        if not isinstance(value, dict):
            return value
        for name, item in value.items():
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise ValueError(f"{name} 必须是正有限数。")
        return value


class _LlmConfig(_SecondsModel):
    request_seconds: float = Field(gt=0, allow_inf_nan=False)


class _RendererConfig(_SecondsModel):
    prepare_seconds: float = Field(gt=0, allow_inf_nan=False)
    draw_seconds: float = Field(gt=0, allow_inf_nan=False)
    resource_close_seconds: float = Field(gt=0, allow_inf_nan=False)


class _EngineConfig(_SecondsModel):
    attempt_seconds: float = Field(gt=0, allow_inf_nan=False)
    close_seconds: float = Field(gt=0, allow_inf_nan=False)


class _GenerationRequestConfig(_SecondsModel):
    fast: float = Field(gt=0, allow_inf_nan=False)
    balanced: float = Field(gt=0, allow_inf_nan=False)
    high: float = Field(gt=0, allow_inf_nan=False)
    manual: float = Field(gt=0, allow_inf_nan=False)


class _FrontendConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    generation_request_seconds: _GenerationRequestConfig
    progress_request_seconds: float = Field(gt=0, allow_inf_nan=False)
    progress_observation_grace_seconds: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def reject_non_numeric_seconds(cls, value: Any) -> Any:
        """拒绝两个标量 timeout 的 YAML 隐式类型."""
        if not isinstance(value, dict):
            return value
        for name in ("progress_request_seconds", "progress_observation_grace_seconds"):
            item = value.get(name)
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise ValueError(f"{name} 必须是正有限数。")
        return value


class _RootConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: str = Field(min_length=1, max_length=100)
    llm: _LlmConfig
    renderer: _RendererConfig
    engine: _EngineConfig
    frontend: _FrontendConfig

    @model_validator(mode="after")
    def validate_timeout_order(self) -> _RootConfig:
        """保证外层足以覆盖三个 direct attempt 的串行最坏边界."""
        inner_attempt_floor = self.llm.request_seconds + self.renderer.prepare_seconds
        if self.engine.attempt_seconds <= inner_attempt_floor:
            raise ValueError(
                "engine.attempt_seconds 必须大于 "
                "llm.request_seconds + renderer.prepare_seconds。"
            )
        parent_floor = 3 * (self.engine.attempt_seconds + self.engine.close_seconds)
        generation = self.frontend.generation_request_seconds.model_dump()
        too_short = sorted(
            name for name, seconds in generation.items() if seconds <= parent_floor
        )
        if too_short:
            raise ValueError(
                "frontend.generation_request_seconds 必须逐档大于三个串行 "
                f"engine attempt + close 的上界 {parent_floor} 秒；过短档位={too_short}。"
            )
        return self


@dataclass(frozen=True, slots=True)
class LlmTimeouts:
    """模型客户端 timeout."""

    request_seconds: float


@dataclass(frozen=True, slots=True)
class RendererTimeouts:
    """Renderer timeout."""

    prepare_seconds: float
    draw_seconds: float
    resource_close_seconds: float


@dataclass(frozen=True, slots=True)
class EngineTimeouts:
    """产品 engine attempt timeout."""

    attempt_seconds: float
    close_seconds: float


@dataclass(frozen=True, slots=True)
class FrontendTimeouts:
    """浏览器请求和观察 timeout."""

    generation_request_seconds: Mapping[str, float]
    progress_request_seconds: float
    progress_observation_grace_seconds: float


@dataclass(frozen=True, slots=True)
class RuntimeTimeouts:
    """完成严格校验的统一 timeout 配置."""

    version: str
    llm: LlmTimeouts
    renderer: RendererTimeouts
    engine: EngineTimeouts
    frontend: FrontendTimeouts


def _read_config(path: Path | None) -> Any:
    """读取显式文件或 wheel 内默认 YAML."""
    if path is not None:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    resource = files("shaderforge.config").joinpath(_DEFAULT_CONFIG_RESOURCE)
    return yaml.safe_load(resource.read_text(encoding="utf-8"))


def load_runtime_timeouts(path: str | Path | None = None) -> RuntimeTimeouts:
    """加载统一 timeout YAML，未知字段或非法边界立即失败."""
    resolved_path = Path(path) if path is not None else None
    try:
        parsed = _RootConfig.model_validate(_read_config(resolved_path))
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError) as exc:
        source = (
            str(resolved_path)
            if resolved_path is not None
            else _DEFAULT_CONFIG_RESOURCE
        )
        raise ValueError(f"runtime timeout 配置无效：{source}: {exc}") from exc

    generation = parsed.frontend.generation_request_seconds.model_dump()
    if frozenset(generation) != _QUALITY_PRESETS:
        raise ValueError("generation_request_seconds 必须恰好包含四个质量档位。")
    return RuntimeTimeouts(
        version=parsed.version,
        llm=LlmTimeouts(request_seconds=parsed.llm.request_seconds),
        renderer=RendererTimeouts(
            prepare_seconds=parsed.renderer.prepare_seconds,
            draw_seconds=parsed.renderer.draw_seconds,
            resource_close_seconds=parsed.renderer.resource_close_seconds,
        ),
        engine=EngineTimeouts(
            attempt_seconds=parsed.engine.attempt_seconds,
            close_seconds=parsed.engine.close_seconds,
        ),
        frontend=FrontendTimeouts(
            generation_request_seconds=MappingProxyType(generation),
            progress_request_seconds=parsed.frontend.progress_request_seconds,
            progress_observation_grace_seconds=(
                parsed.frontend.progress_observation_grace_seconds
            ),
        ),
    )


RUNTIME_TIMEOUTS = load_runtime_timeouts()

__all__ = [
    "RUNTIME_TIMEOUTS",
    "RuntimeTimeouts",
    "load_runtime_timeouts",
]
