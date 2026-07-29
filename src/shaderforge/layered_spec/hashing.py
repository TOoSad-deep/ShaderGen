"""Layered Shader Spec 的稳定 canonical 哈希。."""

from __future__ import annotations

from typing import Any

from shaderforge.layered_spec.models import LayeredShaderSpecV1, LayerProgram
from shaderforge.program_spec import canonical_json, sha256_hex_text


def compute_layer_sha256(
    *,
    layer_id: str,
    role: str,
    z_index: int,
    glsl_body: str,
    uniform_schema: tuple[Any, ...],
    uniform_values: dict[str, Any],
    tunable_manifest: tuple[Any, ...],
) -> str:
    """计算单层全部语义字段的 SHA-256。."""
    canonical = {
        "layer_id": layer_id,
        "role": role,
        "z_index": z_index,
        "glsl_body": glsl_body,
        "uniform_schema": [
            item.to_dict()
            for item in sorted(uniform_schema, key=lambda item: item.name)
        ],
        "uniform_values": {
            name: list(value) if isinstance(value, tuple) else value
            for name, value in sorted(uniform_values.items())
        },
        "tunable_manifest": [
            item.to_dict()
            for item in sorted(tunable_manifest, key=lambda item: item.path)
        ],
    }
    return sha256_hex_text(canonical_json(canonical))


def recompute_layer_sha256(layer: LayerProgram) -> str:
    """从实际 Layer 内容重算哈希，不信任存储的 layer_sha256。."""
    return compute_layer_sha256(
        layer_id=layer.layer_id,
        role=layer.role,
        z_index=layer.z_index,
        glsl_body=layer.glsl_body,
        uniform_schema=layer.uniform_schema,
        uniform_values=dict(layer.uniform_values),
        tunable_manifest=layer.tunable_manifest,
    )


def compute_layered_spec_sha256(
    *,
    schema_version: str,
    plan_sha256: str,
    canvas: Any,
    layers: tuple[LayerProgram, ...],
    author_identity: Any,
    derivation_provenance: Any | None = None,
) -> str:
    """计算有序 Layer、画布、计划与可信作者身份的整体哈希。."""
    canonical = {
        "schema_version": schema_version,
        "plan_sha256": plan_sha256,
        "canvas": canvas.to_dict(),
        "layers": [
            {
                **layer.semantic_dict(),
                "layer_sha256": recompute_layer_sha256(layer),
            }
            for layer in layers
        ],
        "author_identity": author_identity.to_dict(),
    }
    if derivation_provenance is not None:
        canonical["derivation_provenance"] = derivation_provenance.to_dict()
    return sha256_hex_text(canonical_json(canonical))


def recompute_layered_spec_sha256(spec: LayeredShaderSpecV1) -> str:
    """从实际 Spec 内容重算整体哈希。."""
    return compute_layered_spec_sha256(
        schema_version=spec.schema_version,
        plan_sha256=spec.plan_sha256,
        canvas=spec.canvas,
        layers=spec.layers,
        author_identity=spec.author_identity,
        derivation_provenance=spec.derivation_provenance,
    )
