"""LayerPatchV1 的原子 replace-one-layer 应用。."""

from __future__ import annotations

from shaderforge.layered_spec.hashing import (
    compute_layered_spec_sha256,
    recompute_layer_sha256,
    recompute_layered_spec_sha256,
)
from shaderforge.layered_spec.models import (
    LAYER_PATCH_V1_SCHEMA_VERSION,
    LAYERED_SHADER_SPEC_V1_SCHEMA_VERSION,
    LayeredShaderSpecV1,
    LayerPatchV1,
)
from shaderforge.layered_spec.parsing import (
    LayeredSpecError,
    _validate_global_uniforms,
    _validate_resource_limits,
)
from shaderforge.program_spec import AuthorIdentity


def _validate_base_integrity(base_spec: LayeredShaderSpecV1) -> None:
    if base_spec.schema_version != LAYERED_SHADER_SPEC_V1_SCHEMA_VERSION:
        raise LayeredSpecError("invalid_schema_version", "base Spec 版本不受支持。")
    for layer in base_spec.layers:
        if recompute_layer_sha256(layer) != layer.layer_sha256:
            raise LayeredSpecError(
                "layer_hash_mismatch", f"Layer {layer.layer_id} 内容哈希失配。"
            )
    if recompute_layered_spec_sha256(base_spec) != base_spec.layered_spec_sha256:
        raise LayeredSpecError(
            "layered_spec_hash_mismatch", "base Layered Spec 内容哈希失配。"
        )


def apply_layer_patch(
    base_spec: LayeredShaderSpecV1,
    patch: LayerPatchV1,
    author_identity: AuthorIdentity,
) -> LayeredShaderSpecV1:
    """验证并原子替换唯一目标 Layer，返回重新哈希的新 Spec。."""
    _validate_base_integrity(base_spec)
    if patch.schema_version != LAYER_PATCH_V1_SCHEMA_VERSION:
        raise LayeredSpecError("invalid_schema_version", "Patch 版本不受支持。")
    if patch.base_layered_spec_sha256 != base_spec.layered_spec_sha256:
        raise LayeredSpecError("base_hash_mismatch", "Patch base Spec 哈希不匹配。")
    indices = [
        index
        for index, layer in enumerate(base_spec.layers)
        if layer.layer_id == patch.target_layer_id
    ]
    if len(indices) != 1:
        raise LayeredSpecError("target_layer_missing", "Patch 目标 Layer 不存在。")
    index = indices[0]
    previous = base_spec.layers[index]
    if patch.expected_layer_sha256 != previous.layer_sha256:
        raise LayeredSpecError(
            "expected_layer_hash_mismatch", "Patch 目标 Layer 哈希不匹配。"
        )
    if recompute_layer_sha256(patch.replacement) != patch.replacement.layer_sha256:
        raise LayeredSpecError(
            "replacement_hash_mismatch", "replacement Layer 内容哈希失配。"
        )
    if (
        patch.replacement.layer_id,
        patch.replacement.role,
        patch.replacement.z_index,
    ) != (previous.layer_id, previous.role, previous.z_index):
        raise LayeredSpecError(
            "replacement_identity_mismatch",
            "replacement 的 layer_id/role/z_index 必须与旧 Layer 一致。",
        )
    if author_identity.role not in {"refine", "repair"}:
        raise LayeredSpecError(
            "invalid_author_role", "Patch 结果必须绑定 refine 或 repair 身份。"
        )
    if author_identity.plan_sha256 != base_spec.plan_sha256:
        raise LayeredSpecError(
            "author_plan_mismatch", "Patch author identity 未绑定当前 LayerPlan。"
        )
    if author_identity.reference_sha256 != base_spec.author_identity.reference_sha256:
        raise LayeredSpecError(
            "author_reference_mismatch", "Patch author identity 未绑定同一参考图。"
        )
    layers = list(base_spec.layers)
    layers[index] = patch.replacement
    result_layers = tuple(layers)
    _validate_global_uniforms(result_layers)
    _validate_resource_limits(result_layers, base_spec.canvas)
    result_hash = compute_layered_spec_sha256(
        schema_version=LAYERED_SHADER_SPEC_V1_SCHEMA_VERSION,
        plan_sha256=base_spec.plan_sha256,
        canvas=base_spec.canvas,
        layers=result_layers,
        author_identity=author_identity,
    )
    return LayeredShaderSpecV1(
        schema_version=LAYERED_SHADER_SPEC_V1_SCHEMA_VERSION,
        plan_sha256=base_spec.plan_sha256,
        canvas=base_spec.canvas,
        layers=result_layers,
        author_identity=author_identity,
        layered_spec_sha256=result_hash,
    )
