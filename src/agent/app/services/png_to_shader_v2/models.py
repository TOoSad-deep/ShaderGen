"""PNG-to-Shader V2.3 development Service 的版本化传输模型。"""
# ruff: noqa: D415

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import Field, model_validator

from agent.app.states.png_to_shader_v2_state import (
    BudgetVectorV2,
    PngToShaderV2State,
)
from shaderforge.contracts import FrozenModel, NonEmptyString, Sha256Hex
from shaderforge.intent import (
    IntentBuildContext,
    RequestConstraintSet,
    VisualInterpretationV2,
)
from shaderforge.store import ArtifactRefV2

from .real_model import RealModelCallPolicyV1


class PngToShaderV2ServiceConfig(FrozenModel):
    """冻结 development-only Service 行为与七维上限。"""

    schema_version: Literal["png_to_shader_v2_service_config_v1"] = (
        "png_to_shader_v2_service_config_v1"
    )
    execution_mode: Literal["fixture/no-model", "real"] = "fixture/no-model"
    allow_model_calls: bool = False
    real_provider_enabled: bool = False
    production_admission_enabled: Literal[False] = False
    budget_limits: BudgetVectorV2
    real_model_call: RealModelCallPolicyV1 | None = None

    @model_validator(mode="after")
    def _validate_mode(self) -> PngToShaderV2ServiceConfig:
        if self.execution_mode == "fixture/no-model":
            if self.allow_model_calls or self.real_provider_enabled:
                raise ValueError("fixture/no-model 禁止打开模型调用开关。")
            if any(
                (
                    self.budget_limits.model_calls,
                    self.budget_limits.model_tokens,
                    self.budget_limits.cost_usd_micros,
                )
            ):
                raise ValueError("fixture/no-model 的模型调用、token 与成本上限必须为 0。")
            if self.real_model_call is not None:
                raise ValueError("fixture/no-model 禁止配置 real_model_call。")
        elif not (self.allow_model_calls and self.real_provider_enabled):
            raise ValueError("real mode 必须同时打开 allow_model_calls 与 provider 开关。")
        elif self.real_model_call is None:
            raise ValueError("real mode 必须提供显式 real_model_call 身份与调用上限。")
        if self.budget_limits.wall_time_ms <= 0:
            raise ValueError("Service 必须设置正数 wall_time_ms deadline。")
        return self


class PngToShaderV2RequestMetadata(FrozenModel):
    """不含图片大 payload 的显式请求身份。"""

    schema_version: Literal["png_to_shader_v2_request_metadata_v1"] = (
        "png_to_shader_v2_request_metadata_v1"
    )
    request_id: NonEmptyString
    expected_source_sha256: Sha256Hex | None = None
    source_label: NonEmptyString
    source_license: NonEmptyString


@dataclass(frozen=True)
class FixtureIntentInputsV1:
    """测量完成后由 validation fixture 产生的严格 Intent 输入。"""

    request_constraint_set: RequestConstraintSet
    visual_interpretation: VisualInterpretationV2
    intent_context: IntentBuildContext


class PngToShaderV2ResumeContextV1(FrozenModel):
    """从 State 中的 constraint evidence 可发现的恢复组合根。"""

    schema_version: Literal["png_to_shader_v2_resume_context_v1"] = (
        "png_to_shader_v2_resume_context_v1"
    )
    project_id: NonEmptyString
    run_id: NonEmptyString
    source_sha256: Sha256Hex
    config_ref: ArtifactRefV2
    request_metadata_ref: ArtifactRefV2
    measurement_bundle_ref: ArtifactRefV2
    normalized_reference_ref: ArtifactRefV2
    visual_interpretation_ref: ArtifactRefV2
    intent_context_ref: ArtifactRefV2


class PngToShaderV2RunManifestV1(FrozenModel):
    """Service 输出索引；只保存运行身份和 ArtifactRef。"""

    schema_version: Literal["png_to_shader_v2_run_manifest_v1"] = (
        "png_to_shader_v2_run_manifest_v1"
    )
    project_id: NonEmptyString
    run_id: NonEmptyString
    config_ref: ArtifactRefV2
    request_metadata_ref: ArtifactRefV2
    measurement_bundle_ref: ArtifactRefV2
    resume_context_ref: ArtifactRefV2
    request_constraint_set_ref: ArtifactRefV2
    final_phase: NonEmptyString
    final_run_revision: int = Field(ge=0)
    stop_reason: NonEmptyString | None
    objective_best_ref: ArtifactRefV2 | None
    candidate_summary_refs: tuple[ArtifactRefV2, ...]


@dataclass(frozen=True)
class PngToShaderV2DevelopmentResult:
    """调用方可持久化的稳定 Service 结果。"""

    project_id: str
    run_id: str
    final_state: PngToShaderV2State
    run_manifest_ref: ArtifactRefV2
    resume_context_ref: ArtifactRefV2


__all__ = [
    "FixtureIntentInputsV1",
    "PngToShaderV2DevelopmentResult",
    "PngToShaderV2RequestMetadata",
    "PngToShaderV2ResumeContextV1",
    "PngToShaderV2RunManifestV1",
    "PngToShaderV2ServiceConfig",
]
