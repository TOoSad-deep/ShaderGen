"""PNG-to-Shader V1 单元与集成测试共享的结构化样例."""

from __future__ import annotations

import json
from copy import deepcopy

GOLDEN_GLSL = """precision mediump float;
varying vec2 v_uv;
uniform sampler2D u_image;
uniform vec2 u_resolution;
uniform float u_time;
void main() {
  float radius = 0.35;
  vec3 baseColor = vec3(1.0, 0.2, 0.5);
  float mask = 1.0 - smoothstep(radius, radius + 0.01, length(v_uv - vec2(0.5)));
  gl_FragColor = vec4(mix(vec3(1.0), baseColor, mask), 1.0);
}
"""


def analysis_payload() -> dict:
    return {
        "analysis_version": "visual_analysis_v1_2",
        "summary": "白色背景上的粉色圆形主体，左上有弧形高光。",
        "subject": {
            "shape_family": "circle",
            "center_uv": [0.5, 0.5],
            "size_uv": [0.7, 0.7],
            "rotation_degrees": 0.0,
            "foreground_measurement_ref": "foreground_bbox_uv",
            "confidence": 0.96,
        },
        "background": {
            "type": "solid",
            "colors": ["#FFFFFF"],
            "shadow_or_glow": "主体右下方存在淡粉投影。",
            "confidence": 0.9,
        },
        "layers": [
            {
                "layer_id": "background",
                "role": "background",
                "order": 0,
                "region_description": "全画布白色背景。",
                "color_observation": "近纯白。",
                "field_type": "constant",
                "primitive_candidates": ["constant_color"],
                "confidence": 0.99,
            },
            {
                "layer_id": "subject",
                "role": "base_fill",
                "order": 1,
                "region_description": "画面中央圆形区域。",
                "color_observation": "上深下浅的连续粉色场。",
                "field_type": "sdf",
                "primitive_candidates": ["circle_sdf", "position_gradient"],
                "confidence": 0.95,
            },
            {
                "layer_id": "highlight",
                "role": "highlight",
                "order": 2,
                "region_description": "主体左上边缘的短弧。",
                "color_observation": "白色柔和高光。",
                "field_type": "radial",
                "primitive_candidates": ["radial_band", "angular_window"],
                "confidence": 0.92,
            },
        ],
        "coordinate_advice": {
            "position_fields": ["subject"],
            "direction_fields": ["highlight"],
            "radial_fields": ["subject", "highlight"],
            "short_side_normalization_recommended": True,
            "notes": ["宽渐变使用连续位置，避免中心方向接缝。"],
        },
        "regions_of_interest": [
            {
                "region_id": "subject",
                "bbox_uv": [0.15, 0.15, 0.85, 0.85],
                "purpose": "geometry",
                "confidence": 0.95,
            },
            {
                "region_id": "highlight",
                "bbox_uv": [0.2, 0.65, 0.5, 0.88],
                "purpose": "highlight",
                "confidence": 0.9,
            },
        ],
        "representative_probes": [
            {
                "probe_id": "center",
                "uv": [0.5, 0.5],
                "purpose": "主体中心基础颜色。",
                "measurement_ref": "representative_pixels.center",
            }
        ],
        "strategy_candidates": [
            {
                "strategy": "sdf_layered_2d",
                "rank": 1,
                "reason": "轮廓规则且内部效果可用少量解析场分层表达。",
                "required_layers": ["background", "subject", "highlight"],
                "complexity": "low",
            }
        ],
        "risks": ["高光需要同时约束径向位置和角向长度。"],
        "unknowns": [],
    }


def parameter_manifest() -> list[dict]:
    return [
        {
            "name": "radius",
            "semantic_role": "radius",
            "problem_domain": "geometry",
            "current_value": "0.35",
            "safe_range": "0.2..0.48",
            "affected_regions": ["subject"],
        },
        {
            "name": "baseColor",
            "semantic_role": "base_color",
            "problem_domain": "base_color_field",
            "current_value": "vec3(1.0,0.2,0.5)",
            "safe_range": "0..1 per channel",
            "affected_regions": ["subject"],
        },
    ]


def author_payload(mode: str = "initial") -> dict:
    versions = {
        "initial": "shader_author_initial_v1_1",
        "compile_repair": "shader_author_compile_repair_v1_1",
        "visual_refine": "shader_author_visual_refine_v1",
    }
    domains = {
        "initial": "initial_build",
        "compile_repair": "runtime_compile",
        "visual_refine": "highlight",
    }
    payload = {
        "author_version": versions[mode],
        "mode": mode,
        "base_candidate_id": "candidate-best" if mode == "visual_refine" else None,
        "glsl": GOLDEN_GLSL,
        "strategy_summary": "使用圆形 SDF、连续颜色场和解析弧形高光分层合成。",
        "implemented_layers": ["background", "subject", "highlight"],
        "parameter_manifest": parameter_manifest(),
        "changed_problem_domain": domains[mode],
        "changed_parameters": ["highlightWidth"] if mode == "visual_refine" else [],
        "protected_regions": ["subject"] if mode == "visual_refine" else [],
        "expected_metric_changes": ["highlight ROI loss 降低"],
        "known_limitations": ["不处理细微折射纹理。"],
    }
    return payload


def review_payload(candidate_id: str = "candidate-best") -> dict:
    return {
        "review_version": "visual_critic_v1",
        "candidate_id": candidate_id,
        "overall_assessment": "主体几何接近，高光仍偏长。",
        "primary_problem_domain": "highlight",
        "evidence": [
            {
                "region_id": "highlight",
                "observation": "当前白色弧线延伸过长。",
                "reference_vs_render": "参考图角向跨度更短。",
                "metric_refs": ["roi_losses.highlight"],
                "severity": "medium",
            }
        ],
        "recommended_changes": [
            {
                "target": "highlightWidth",
                "action": "narrow",
                "direction": "收窄角向窗口，保持径向位置。",
                "reason": "直接缩短高光且不影响主体几何。",
            }
        ],
        "protected_regions": ["subject"],
        "do_not_change": ["radius", "baseColor"],
        "stop_recommendation": "continue",
        "confidence": 0.9,
    }


def json_text(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def changed(value: dict, **updates) -> dict:
    result = deepcopy(value)
    result.update(updates)
    return result
