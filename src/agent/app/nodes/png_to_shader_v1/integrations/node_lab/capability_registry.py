"""PNG-to-Shader V1 的确定性 capability descriptor 目录."""

from __future__ import annotations

from dataclasses import dataclass

from nodelab.capabilities import CapabilityRegistry
from nodelab.models import CapabilityDescriptor

PIPELINE_ID = "png_to_shader_v1"


@dataclass(frozen=True)
class _CapabilitySpec:
    capability_id: str
    summary: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    source_ref: str
    metrics: tuple[str, ...]
    requires_browser: bool = False


_SPECS = (
    _CapabilitySpec(
        "normalize-target",
        "把上传图片规范化为白底 RGB PNG Artifact。",
        ("source_artifact_id",),
        ("normalized_artifact", "image_sha256"),
        "src/shaderforge/analysis/measurements.py",
        ("schema_pass", "duration_ms", "output_sha256"),
    ),
    _CapabilitySpec(
        "measure-target",
        "从参考 PNG 提取确定性 TargetMeasurements。",
        ("reference_artifact_id",),
        ("target_measurements", "measurements_artifact"),
        "src/shaderforge/analysis/measurements.py",
        ("schema_pass", "duration_ms", "measurements_sha256"),
    ),
    _CapabilitySpec(
        "validate-shader",
        "执行 WebGL1 无贴图静态 Shader 契约校验。",
        ("shader_artifact_id",),
        ("validation",),
        "src/shaderforge/validation/shader_validator.py",
        ("expected_outcome_pass", "violation_codes_pass", "duration_ms"),
    ),
    _CapabilitySpec(
        "render-shader",
        "在真实或注入 Renderer 中编译并渲染 Shader。",
        ("shader_artifact_id", "width", "height"),
        ("render", "render_artifact", "diagnostics_artifact"),
        "src/shaderforge/rendering/webgl1_renderer.py",
        ("compile_success", "render_success", "duration_ms", "pixel_sha256"),
        True,
    ),
    _CapabilitySpec(
        "evaluate-render",
        "用 Basic Oracle 比较参考 PNG 与渲染 PNG。",
        ("reference_artifact_id", "render_artifact_id"),
        ("score", "metrics_artifact"),
        "src/shaderforge/evaluation/oracle.py",
        ("schema_pass", "total_loss", "duration_ms"),
    ),
    _CapabilitySpec(
        "select-current-best",
        "按 AcceptancePolicy 判断候选是否替换 current_best。",
        ("candidate",),
        ("decision",),
        "src/shaderforge/evaluation/selection.py",
        ("decision_pass", "duration_ms"),
    ),
    _CapabilitySpec(
        "decide-after-render",
        "复用生产路由决定 select、compile_repair 或 finalize。",
        ("render_status", "budget_policy"),
        ("next_action", "stop_reason"),
        "src/agent/app/graphs/png_to_shader_v1_routing.py",
        ("decision_pass", "duration_ms"),
    ),
    _CapabilitySpec(
        "decide-after-selection",
        "复用生产路由决定 visual_critic 或 finalize。",
        ("current_best_total_loss", "budget_policy", "acceptance_policy"),
        ("next_action", "stop_reason"),
        "src/agent/app/graphs/png_to_shader_v1_routing.py",
        ("decision_pass", "duration_ms"),
    ),
)

_ARTIFACT_ID_SCHEMA: dict[str, object] = {
    "type": "string",
    "minLength": 1,
    "maxLength": 128,
    "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
}


def _input_schema(spec: _CapabilitySpec) -> dict[str, object]:
    properties: dict[str, object] = {
        field: dict(_ARTIFACT_ID_SCHEMA) if field.endswith("_artifact_id") else {}
        for field in spec.inputs
    }
    optional_by_capability: dict[str, dict[str, object]] = {
        "normalize-target": {
            "max_long_side": {"type": "integer", "minimum": 1, "maximum": 1024}
        },
        "measure-target": {
            "max_long_side": {"type": "integer", "minimum": 1, "maximum": 1024}
        },
        "validate-shader": {
            "max_shader_chars": {"type": "integer", "minimum": 1, "maximum": 30_000}
        },
        "evaluate-render": {"metric_weights": {"type": "object"}},
        "select-current-best": {
            "current_best": {"type": ["object", "null"]},
            "acceptance_policy": {"type": "object"},
        },
    }
    properties.update(optional_by_capability.get(spec.capability_id, {}))
    if spec.capability_id == "render-shader":
        properties["width"] = {"type": "integer", "minimum": 1, "maximum": 1024}
        properties["height"] = {"type": "integer", "minimum": 1, "maximum": 1024}
    return {
        "type": "object",
        "properties": properties,
        "required": list(spec.inputs),
        "additionalProperties": False,
    }


def _output_schema(spec: _CapabilitySpec) -> dict[str, object]:
    required = (
        ["next_action"]
        if spec.capability_id.startswith("decide-after-")
        else list(spec.outputs)
    )
    return {
        "type": "object",
        "properties": {field: {} for field in spec.outputs},
        "required": required,
        "additionalProperties": True,
    }


def build_png_to_shader_v1_capability_registry() -> CapabilityRegistry:
    """构造 V1 的八个确定性 capability descriptor."""
    return CapabilityRegistry(
        CapabilityDescriptor(
            pipeline_id=PIPELINE_ID,
            capability_id=spec.capability_id,
            summary=spec.summary,
            requires_browser=spec.requires_browser,
            cold_start_sensitive=spec.requires_browser,
            benchmark_profiles=(
                ["node", "renderer_cold", "renderer_warm"]
                if spec.requires_browser
                else ["micro", "node"]
            ),
            benchmark_metrics=list(spec.metrics),
            source_ref=spec.source_ref,
            input_schema=_input_schema(spec),
            output_schema=_output_schema(spec),
        )
        for spec in _SPECS
    )


__all__ = ["build_png_to_shader_v1_capability_registry"]
