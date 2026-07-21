"""从 Intent IR 确定性匹配三个有限模板并生成 SeedPlan。."""

from __future__ import annotations

from shaderforge.intent import IntentIR, ObjectIntent, VisualLayerIntent

from .models import (
    BaseFillKind,
    GeometryKind,
    LayerBindingV1,
    SeedPlanV1,
    SeedRole,
    TemplateMatchV1,
)

_ROLE_PRIMITIVES: dict[str, tuple[str, ...]] = {
    "background": ("gaussian_color_lobe",),
    "shadow": ("shadow",),
    "base_fill": ("solid_fill", "linear_gradient"),
    "color_lobe": ("gaussian_color_lobe",),
    "haze": ("glow",),
    "rim": ("rim_band",),
    "outline": ("outline_band",),
    "highlight": ("arc_highlight",),
    "detail": ("arc_highlight",),
    "glow": ("glow",),
}


def _artifact_key(ref: object) -> tuple[object, ...]:
    return (
        getattr(ref, "sha256"),
        getattr(ref, "kind"),
        getattr(ref, "schema_version"),
        getattr(ref, "content_type"),
        getattr(ref, "size_bytes"),
        getattr(ref, "artifact_id"),
    )


def _primary_object(intent: IntentIR) -> ObjectIntent:
    return min(intent.objects, key=lambda item: (-item.area_ratio, item.object_id))


def _primary_geometry(intent: IntentIR) -> GeometryKind:
    subject = _primary_object(intent)
    axes = sorted(subject.axes_uv)
    ratio = axes[1] / max(axes[0], 1e-9)
    if subject.topology in {"ring", "hollow", "open"} or ratio > 1.8:
        return "rounded_rect_sdf"
    if ratio > 1.12:
        return "ellipse_sdf"
    return "circle_sdf"


def _alternate_geometry(primary: GeometryKind) -> GeometryKind:
    alternatives: dict[GeometryKind, GeometryKind] = {
        "circle_sdf": "ellipse_sdf",
        "ellipse_sdf": "rounded_rect_sdf",
        "rounded_rect_sdf": "circle_sdf",
    }
    return alternatives[primary]


def _template_id(
    role: SeedRole,
    geometry: GeometryKind,
    base_fill: BaseFillKind,
) -> str:
    return f"seed_{role}_{geometry}_{base_fill}_v1"


def match_seed_templates(
    intent: IntentIR,
) -> tuple[
    TemplateMatchV1,
    TemplateMatchV1,
    TemplateMatchV1,
]:
    """只按 Intent 结构匹配冻结的三个默认模板，不调用模型。."""
    primary = _primary_geometry(intent)
    alternative = _alternate_geometry(primary)
    enabled = tuple(item.layer_id for item in intent.layers if item.required)
    if not enabled:
        raise ValueError("Intent 没有 required layer，无法匹配 Seed 模板。")
    return (
        TemplateMatchV1(
            seed_role="minimum_complexity",
            template_id=_template_id("minimum_complexity", primary, "solid_fill"),
            geometry_kind=primary,
            base_fill_kind="solid_fill",
            enabled_layer_ids=enabled,
            reason_codes=("required_layers_only", "simplest_compatible_geometry"),
        ),
        TemplateMatchV1(
            seed_role="semantic_enhancement",
            template_id=_template_id(
                "semantic_enhancement", primary, "linear_gradient"
            ),
            geometry_kind=primary,
            base_fill_kind="linear_gradient",
            enabled_layer_ids=enabled,
            reason_codes=("required_layers_only", "gradient_base_enhancement"),
        ),
        TemplateMatchV1(
            seed_role="alternate_structure",
            template_id=_template_id("alternate_structure", alternative, "solid_fill"),
            geometry_kind=alternative,
            base_fill_kind="solid_fill",
            enabled_layer_ids=enabled,
            reason_codes=("required_layers_only", "alternate_geometry_family"),
        ),
    )


def _primitive_for_layer(
    intent: IntentIR,
    layer: VisualLayerIntent,
    *,
    base_fill_kind: BaseFillKind,
) -> str:
    if layer.role == "base_fill":
        return base_fill_kind
    allowed = _ROLE_PRIMITIVES[layer.role]
    candidates = {
        item.candidate_id: item
        for item in intent.primitive_candidates
        if item.layer_id == layer.layer_id and item.primitive_id in allowed
    }
    referenced = [
        candidates[candidate_id]
        for candidate_id in layer.primitive_candidate_ids
        if candidate_id in candidates
    ]
    if not referenced:
        return allowed[0]
    return min(
        referenced,
        key=lambda item: (-item.confidence, item.primitive_id, item.candidate_id),
    ).primitive_id


def _bindings(intent: IntentIR, match: TemplateMatchV1) -> tuple[LayerBindingV1, ...]:
    enabled = set(match.enabled_layer_ids)
    return tuple(
        LayerBindingV1(
            layer_id=layer.layer_id,
            layer_order=layer.order,
            role=layer.role,
            object_ref=layer.object_ref,
            primitive_id=_primitive_for_layer(
                intent,
                layer,
                base_fill_kind=match.base_fill_kind,
            ),
            enabled=layer.layer_id in enabled,
        )
        for layer in intent.layers
    )


def build_seed_plans(
    intent: IntentIR,
    *,
    random_seed: int = 0,
) -> tuple[SeedPlanV1, SeedPlanV1, SeedPlanV1]:
    """为单个 Intent 确定性生成恰好三个 rule SeedPlan。."""
    if random_seed < 0 or random_seed > 9_223_372_036_854_775_805:
        raise ValueError("random_seed 必须允许连续分配三个有符号 64-bit seed。")
    matches = match_seed_templates(intent)
    evidence_refs = tuple(sorted(set(intent.evidence_refs), key=_artifact_key))
    plans = tuple(
        SeedPlanV1(
            seed_role=match.seed_role,
            intent_id=intent.intent_id,
            target_hypothesis_id=intent.target_hypothesis_id,
            target_hypothesis_hash=intent.target_hypothesis_hash,
            template_id=match.template_id,
            layer_bindings=_bindings(intent, match),
            source="rule",
            random_seed=random_seed + index,
            evidence_refs=evidence_refs,
        )
        for index, match in enumerate(matches)
    )
    return (plans[0], plans[1], plans[2])


__all__ = ["build_seed_plans", "match_seed_templates"]
