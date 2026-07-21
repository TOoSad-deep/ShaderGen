from __future__ import annotations

import json
from copy import deepcopy
from io import BytesIO
from itertools import combinations
from typing import Any, TypedDict, cast

import pytest
from PIL import Image

from shaderforge.analysis import BBoxUv
from shaderforge.compiler import (
    CompilationProduct,
    CompilerDefectError,
    compile_diagnostic_passes,
    compile_effect_genome,
)
from shaderforge.genome import TypedEffectGenome
from shaderforge.intent import (
    CanvasIntent,
    InstanceIntent,
    IntentIR,
    ObjectIntent,
    PrimitiveCandidate,
    RegionIntent,
    StrategyHypothesis,
    VisualLayerIntent,
)
from shaderforge.rendering import PlaywrightWebGL1Renderer
from shaderforge.seeding import expand_seed_plans
from shaderforge.store import ArtifactRefV2

_CAPTURES_PER_SEED = 5
_MAX_NORMALIZED_RGB_MAE = 1.0 / 255.0


class _CaptureResult(TypedDict):
    capture_index: int
    render_success: bool
    compile_link_draw_success: bool
    vertex_log: str
    fragment_log: str
    link_log: str
    draw_error: str | None
    console_errors: tuple[str, ...]
    png_decodable: bool
    png_size: tuple[int, int] | None
    png_mode: str | None
    png_error: str | None
    image_sha256: str | None
    gl_version: str | None


class _CaseResult(TypedDict):
    seed_role: str
    template_id: str
    semantic_genome_hash: str
    glsl_sha256: str
    capture_count: int
    captures: list[_CaptureResult]
    pairwise_rgb_mae: tuple[float, ...]
    max_rgb_mae: float | None


def _normalized_rgb_mae(left: bytes, right: bytes) -> float:
    assert len(left) == len(right)
    return sum(
        abs(first - second) for first, second in zip(left, right, strict=True)
    ) / (len(left) * 255.0)


def _artifact_ref(
    name: str,
    digit: str,
    *,
    kind: str,
    schema_version: str,
    content_type: str = "application/json",
    size_bytes: int = 10,
) -> ArtifactRefV2:
    return ArtifactRefV2(
        artifact_id=f"artifact_{name}",
        sha256=digit * 64,
        kind=kind,
        schema_version=schema_version,
        content_type=content_type,
        size_bytes=size_bytes,
    )


def _intent() -> IntentIR:
    mask = _artifact_ref(
        "v2_webgl_mask",
        "1",
        kind="subject_mask",
        schema_version="subject_mask_v1",
        content_type="image/png",
        size_bytes=16,
    )
    evidence = _artifact_ref(
        "v2_webgl_evidence",
        "2",
        kind="visual_interpretation",
        schema_version="visual_interpretation_v2_1",
    )
    return IntentIR(
        intent_id="intent-v2-webgl-conformance",
        target_sha256="a" * 64,
        target_hypothesis_id="hypothesis-v2-webgl-conformance",
        target_hypothesis_hash="b" * 64,
        constraint_set_hash="c" * 64,
        canvas=CanvasIntent(
            contract_id="webgl1_static_no_texture_v1",
            image_size=(64, 64),
        ),
        objects=(
            ObjectIntent(
                object_id="subject",
                subject_mask_ref=mask,
                instances=(
                    InstanceIntent(
                        instance_id="instance_0000",
                        instance_index=0,
                        mask_ref=mask,
                        bbox_uv=BBoxUv(
                            min_x=0.2, min_y=0.2, max_x=0.8, max_y=0.8
                        ),
                        center_uv=(0.5, 0.5),
                        area_ratio=0.28,
                        axes_uv=(0.3, 0.3),
                        orientation_rad=0.0,
                        fill_topology="solid",
                        component_count=1,
                        hole_count=0,
                    ),
                ),
                bbox_uv=BBoxUv(min_x=0.2, min_y=0.2, max_x=0.8, max_y=0.8),
                center_uv=(0.5, 0.5),
                area_ratio=0.28,
                axes_uv=(0.3, 0.3),
                orientation_rad=0.0,
                topology="solid",
                component_count=1,
                instance_count=1,
                hole_count=0,
                confidence=0.95,
                evidence_refs=(evidence,),
            ),
        ),
        layers=(
            VisualLayerIntent(
                layer_id="layer-base",
                role="base_fill",
                order=0,
                object_ref="subject",
                required=True,
                source="policy",
                confidence=1.0,
                region_description="主体内部",
                primitive_candidate_ids=("primitive-base",),
            ),
            VisualLayerIntent(
                layer_id="layer-highlight",
                role="highlight",
                order=1,
                object_ref="subject",
                required=True,
                source="model",
                confidence=0.8,
                region_description="主体上缘",
                primitive_candidate_ids=("primitive-highlight",),
                evidence_refs=(evidence,),
            ),
        ),
        relations=(),
        regions=(
            RegionIntent(
                region_id="subject",
                bbox_uv=BBoxUv(min_x=0.2, min_y=0.2, max_x=0.8, max_y=0.8),
                area_ratio=0.28,
                mean_lab=(60.0, 20.0, 5.0),
            ),
        ),
        probes=(),
        hard_constraints=(),
        soft_preferences=(),
        primitive_candidates=(
            PrimitiveCandidate(
                candidate_id="primitive-base",
                primitive_id="solid_fill",
                layer_id="layer-base",
                confidence=0.95,
                evidence_refs=(evidence,),
            ),
            PrimitiveCandidate(
                candidate_id="primitive-highlight",
                primitive_id="arc_highlight",
                layer_id="layer-highlight",
                confidence=0.8,
                evidence_refs=(evidence,),
            ),
        ),
        strategy_hypotheses=(
            StrategyHypothesis(
                strategy_id="strategy-layered",
                template_ids=("layered-shape",),
                required_layer_ids=("layer-base", "layer-highlight"),
                complexity="medium",
                confidence=0.85,
                evidence_refs=(evidence,),
            ),
        ),
        uncertainties=(),
        evidence_refs=(mask, evidence),
    )


def _fully_occluded_base_genome() -> TypedEffectGenome:
    """构造下游全不透明 fill，验证 raw node alpha 不能冒充可见贡献。"""
    genome = expand_seed_plans(_intent()).expanded_seeds[0].genome
    payload = genome.model_dump(mode="json")
    nodes = cast(list[dict[str, Any]], payload["nodes"])
    edges = cast(list[dict[str, Any]], payload["edges"])
    parameters = cast(list[dict[str, Any]], payload["parameters"])
    subject_geometry = next(
        item for item in nodes if item["node_id"] == "geometry_subject"
    )
    subject_geometry["semantic_role"] = "instance_0000_geometry"
    occluder_geometry = deepcopy(subject_geometry)
    occluder_geometry["node_id"] = "geometry_occluder"
    occluder_geometry["semantic_role"] = "occluder_geometry"
    occluder_geometry["sibling_ordinal"] = 1
    occluder_geometry["parameter_bindings"][0]["parameter_path"] = (
        "occluder.center"
    )
    occluder_geometry["parameter_bindings"][1]["parameter_path"] = (
        "occluder.radius"
    )
    base = deepcopy(next(item for item in nodes if item["node_id"] == "layer_00_base_fill"))
    base["node_id"] = "layer_99_detail"
    base["semantic_role"] = "layer_detail"
    base["parameter_bindings"][0]["parameter_path"] = "layers.occluder.color"
    composite = deepcopy(
        next(item for item in nodes if item["node_id"] == "composite_01")
    )
    composite["node_id"] = "composite_99"
    composite["semantic_role"] = "composite_99"
    composite["sibling_ordinal"] = 1
    composite["parameter_bindings"][0]["parameter_path"] = "composite.99.opacity"
    nodes.extend((occluder_geometry, base, composite))
    edges[:] = [
        item
        for item in edges
        if not (
            item["source_node_id"] == "composite_01"
            and item["target_node_id"] == "output_color"
        )
    ]
    edges.extend(
        (
            {
                "source_node_id": "geometry_occluder",
                "source_port": "sdf",
                "target_node_id": "layer_99_detail",
                "target_port": "mask",
                "sdf_to_mask_conversion": "analytic_fixed_width_v1",
            },
            {
                "source_node_id": "composite_01",
                "source_port": "color",
                "target_node_id": "composite_99",
                "target_port": "background",
                "sdf_to_mask_conversion": None,
            },
            {
                "source_node_id": "layer_99_detail",
                "source_port": "color",
                "target_node_id": "composite_99",
                "target_port": "foreground",
                "sdf_to_mask_conversion": None,
            },
            {
                "source_node_id": "composite_99",
                "source_port": "color",
                "target_node_id": "output_color",
                "target_port": "color",
                "sdf_to_mask_conversion": None,
            },
        )
    )
    occluder_color = deepcopy(
        next(item for item in parameters if item["path"] == "layers.layer-base.color")
    )
    occluder_color["path"] = "layers.occluder.color"
    occluder_color["value"] = [0.1, 0.2, 0.3, 1.0]
    occluder_center = deepcopy(
        next(item for item in parameters if item["path"] == "shape.center")
    )
    occluder_center["path"] = "occluder.center"
    occluder_radius = deepcopy(
        next(item for item in parameters if item["path"] == "shape.radius")
    )
    occluder_radius["path"] = "occluder.radius"
    occluder_radius["value"] = 2.0
    occluder_radius["max_value"] = 2.0
    occluder_opacity = deepcopy(
        next(item for item in parameters if item["path"] == "composite.01.opacity")
    )
    occluder_opacity["path"] = "composite.99.opacity"
    occluder_opacity["value"] = 1.0
    parameters.extend(
        (occluder_center, occluder_radius, occluder_color, occluder_opacity)
    )
    payload["genome_id"] = "genome-fully-occluded-base"
    payload["strategy"] = "test_fully_occluded_base_v1"
    return TypedEffectGenome.model_validate_json(json.dumps(payload), strict=True)


@pytest.mark.anyio
async def test_v2_three_seeds_compile_and_render_deterministically() -> None:
    """真实 Chromium/WebGL1 门禁；浏览器不可用必须暴露为测试失败。"""
    intent = _intent()
    first_expansion = expand_seed_plans(intent, random_seed=41)
    replay_expansion = expand_seed_plans(intent, random_seed=41)

    assert first_expansion.diversity.gate_passed is True
    assert len(set(first_expansion.diversity.semantic_genome_hashes)) == 3
    structural_signatures = {
        (
            expanded.plan.template_id,
            expanded.genome_hashes.topology_hash,
            tuple(
                binding.layer_id
                for binding in expanded.plan.layer_bindings
                if binding.enabled
            ),
        )
        for expanded in first_expansion.expanded_seeds
    }
    assert len(structural_signatures) >= 2

    first_products: list[CompilationProduct] = []
    replay_products: list[CompilationProduct] = []
    compile_failures: list[dict[str, object]] = []
    for first_seed, replay_seed in zip(
        first_expansion.expanded_seeds,
        replay_expansion.expanded_seeds,
        strict=True,
    ):
        try:
            first_products.append(compile_effect_genome(first_seed.genome))
            replay_products.append(compile_effect_genome(replay_seed.genome))
        except CompilerDefectError as exc:
            compile_failures.append(
                {
                    "seed_role": first_seed.plan.seed_role,
                    "template_id": first_seed.plan.template_id,
                    "semantic_genome_hash": (
                        first_seed.genome_hashes.semantic_genome_hash
                    ),
                    "compiler_error_code": exc.code,
                    "compiler_diagnostics": exc.diagnostics,
                }
            )
    assert not compile_failures, json.dumps(
        compile_failures,
        ensure_ascii=False,
        indent=2,
    )
    assert len(first_products) == len(replay_products) == 3
    for first, replay in zip(first_products, replay_products, strict=True):
        assert first.glsl_source.encode("utf-8") == replay.glsl_source.encode("utf-8")
        assert first.glsl_sha256 == replay.glsl_sha256
        assert first.node_line_map == replay.node_line_map
        assert first.compiler_parameter_table == replay.compiler_parameter_table

    case_results: list[_CaseResult] = []
    async with PlaywrightWebGL1Renderer() as renderer:
        for expanded, product in zip(
            first_expansion.expanded_seeds,
            first_products,
            strict=True,
        ):
            captures: list[_CaptureResult] = []
            rgb_frames: list[bytes] = []
            for capture_index in range(_CAPTURES_PER_SEED):
                rendered = await renderer.render(product.glsl_source, 64, 64)
                png_decodable = False
                png_size: tuple[int, int] | None = None
                png_mode: str | None = None
                png_error: str | None = None
                if rendered.image_bytes is not None:
                    try:
                        with Image.open(BytesIO(rendered.image_bytes)) as image:
                            image.load()
                            png_size = image.size
                            png_mode = image.mode
                            png_decodable = image.format == "PNG" and image.size == (
                                64,
                                64,
                            )
                            if png_decodable:
                                rgb_frames.append(image.convert("RGB").tobytes())
                    except (OSError, ValueError) as exc:
                        png_error = f"{type(exc).__name__}: {exc}"
                captures.append(
                    {
                        "capture_index": capture_index,
                        "render_success": rendered.success,
                        "compile_link_draw_success": rendered.compile.success,
                        "vertex_log": rendered.compile.vertex_log,
                        "fragment_log": rendered.compile.fragment_log,
                        "link_log": rendered.compile.link_log,
                        "draw_error": rendered.compile.draw_error,
                        "console_errors": rendered.console_errors,
                        "png_decodable": png_decodable,
                        "png_size": png_size,
                        "png_mode": png_mode,
                        "png_error": png_error,
                        "image_sha256": rendered.image_sha256,
                        "gl_version": (
                            rendered.metadata.gl_version
                            if rendered.metadata is not None
                            else None
                        ),
                    }
                )
            pairwise_mae = tuple(
                _normalized_rgb_mae(left, right)
                for left, right in combinations(rgb_frames, 2)
            )
            max_rgb_mae = max(pairwise_mae) if pairwise_mae else None
            case_results.append(
                {
                    "seed_role": expanded.plan.seed_role,
                    "template_id": expanded.plan.template_id,
                    "semantic_genome_hash": expanded.genome_hashes.semantic_genome_hash,
                    "glsl_sha256": product.glsl_sha256,
                    "capture_count": len(captures),
                    "captures": captures,
                    "pairwise_rgb_mae": pairwise_mae,
                    "max_rgb_mae": max_rgb_mae,
                }
            )

    failures = [
        result
        for result in case_results
        if not (
            result["capture_count"] == _CAPTURES_PER_SEED
            and all(
                capture["render_success"]
                and capture["compile_link_draw_success"]
                and capture["png_decodable"]
                and not capture["draw_error"]
                and not capture["console_errors"]
                and capture["gl_version"]
                for capture in result["captures"]
            )
            and result["max_rgb_mae"] is not None
            and result["max_rgb_mae"] <= _MAX_NORMALIZED_RGB_MAE
        )
    ]
    assert not failures, json.dumps(failures, ensure_ascii=False, indent=2)
    assert len(case_results) == 3


@pytest.mark.anyio
async def test_v2_visible_delta_diagnostics_compile_link_and_draw_in_webgl1() -> None:
    """全部 instance/layer ablation pass 必须通过真实 WebGL1 编译与绘制。"""
    genome = expand_seed_plans(_intent()).expanded_seeds[0].genome
    diagnostics = compile_diagnostic_passes(genome)

    async with PlaywrightWebGL1Renderer() as renderer:
        results = [
            (item.pass_id, await renderer.render(item.glsl_source, 64, 64))
            for item in diagnostics.passes
        ]

    failures = {
        pass_id: {
            "fragment_log": rendered.compile.fragment_log,
            "link_log": rendered.compile.link_log,
            "draw_error": rendered.compile.draw_error,
            "console_errors": rendered.console_errors,
        }
        for pass_id, rendered in results
        if not rendered.success or not rendered.compile.success
    }
    assert not failures, json.dumps(failures, ensure_ascii=False, indent=2)


@pytest.mark.anyio
async def test_v2_occluded_layer_visible_delta_is_zero() -> None:
    """下游 opaque layer 完全遮挡时，base raw alpha 非零但 final delta 必须为零。"""
    diagnostics = compile_diagnostic_passes(_fully_occluded_base_genome())
    base_pass = next(
        item for item in diagnostics.passes if item.pass_id == "layer_base_fill_visible_delta"
    )

    async with PlaywrightWebGL1Renderer() as renderer:
        rendered = await renderer.render(base_pass.glsl_source, 64, 64)

    assert rendered.success, rendered.compile.fragment_log
    assert rendered.image_bytes is not None
    with Image.open(BytesIO(rendered.image_bytes)) as image:
        image.load()
        assert max(image.convert("RGB").tobytes()) == 0
        assert set(image.convert("RGBA").getchannel("A").tobytes()) == {255}
