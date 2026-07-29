"""契约对象的规范化 JSON 与 SHA-256 内容寻址.

所有哈希都由可信层对规范化后的内容重算，绝不信任模型自报值。
canonical JSON 固定为 UTF-8、key 排序、无多余空白，保证同一语义对象
在任何环境下得到同一哈希。
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from shaderforge.program_spec.models import (
    LayerPlanV1,
    ShaderProgramSpecV1,
    TunableParameter,
    UniformDeclaration,
)


def canonical_json(value: Any) -> str:
    """返回确定性 canonical JSON 文本（key 排序、紧凑分隔符）."""
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_hex_text(text: str) -> str:
    """返回 UTF-8 文本的 SHA-256 十六进制摘要."""
    return sha256(text.encode("utf-8")).hexdigest()


def compute_source_sha256(fragment_source: str) -> str:
    """返回规范化 GLSL 源码的内容哈希."""
    return sha256_hex_text(fragment_source)


def _binding_canonical_dict(
    uniform_schema: tuple[UniformDeclaration, ...],
    uniform_values: dict[str, Any],
) -> dict[str, Any]:
    return {
        "uniform_schema": [
            declaration.to_dict()
            for declaration in sorted(uniform_schema, key=lambda item: item.name)
        ],
        "uniform_values": {
            name: (list(value) if isinstance(value, tuple) else value)
            for name, value in sorted(uniform_values.items())
        },
    }


def compute_binding_sha256(
    uniform_schema: tuple[UniformDeclaration, ...],
    uniform_values: dict[str, Any],
) -> str:
    """返回 uniform 声明与初值绑定的内容哈希."""
    return sha256_hex_text(
        canonical_json(_binding_canonical_dict(uniform_schema, uniform_values))
    )


def _spec_semantic_dict(spec: ShaderProgramSpecV1) -> dict[str, Any]:
    """返回参与 spec_sha256 的语义与身份字段，只排除 validation_attestation.

    组件哈希从实际内容重算，保证篡改源码、uniform 绑定或 author_identity
    任一字段（reference/plan/instruction/model/prompt/sampling/role/
    parent/content_type/input_context）后重算结果与存储的 spec_sha256 失配。
    """
    canonical = {
        "schema_version": spec.schema_version,
        "renderer_contract_id": spec.renderer_contract_id,
        "source_sha256": compute_source_sha256(spec.fragment_source),
        "binding_sha256": compute_binding_sha256(
            spec.uniform_schema, dict(spec.uniform_values)
        ),
        "tunable_manifest": [
            parameter.to_dict()
            for parameter in sorted(spec.tunable_manifest, key=lambda item: item.path)
        ],
        "canvas": spec.canvas.to_dict(),
        "author_identity": spec.author_identity.to_dict(),
    }
    if spec.derivation_provenance is not None:
        canonical["derivation_provenance"] = spec.derivation_provenance.to_dict()
    return canonical


def compute_spec_sha256(
    *,
    schema_version: str,
    renderer_contract_id: str,
    source_sha256: str,
    binding_sha256: str,
    tunable_manifest: tuple[TunableParameter, ...],
    canvas: Any,
    author_identity: Any,
    derivation_provenance: Any | None = None,
) -> str:
    """返回整体语义与身份字段的内容哈希，只排除 validation_attestation.

    canonical ``author_identity``（reference/plan/instruction/model_ref/
    prompt_version/sampling_params/role/parent_spec_sha256/
    reference_content_type/input_context_sha256）全部参与哈希，
    任何身份字段篡改都会导致重算失配与 attestation 失效。
    """
    canonical = {
        "schema_version": schema_version,
        "renderer_contract_id": renderer_contract_id,
        "source_sha256": source_sha256,
        "binding_sha256": binding_sha256,
        "tunable_manifest": [
            parameter.to_dict()
            for parameter in sorted(tunable_manifest, key=lambda item: item.path)
        ],
        "canvas": canvas.to_dict(),
        "author_identity": author_identity.to_dict(),
    }
    if derivation_provenance is not None:
        canonical["derivation_provenance"] = derivation_provenance.to_dict()
    return sha256_hex_text(canonical_json(canonical))


def recompute_spec_sha256(spec: ShaderProgramSpecV1) -> str:
    """对既有 Spec 重算 spec_sha256，用于篡改失配检测."""
    return sha256_hex_text(canonical_json(_spec_semantic_dict(spec)))


def recompute_source_sha256(spec: ShaderProgramSpecV1) -> str:
    """对既有 Spec 重算 source_sha256."""
    return compute_source_sha256(spec.fragment_source)


def recompute_binding_sha256(spec: ShaderProgramSpecV1) -> str:
    """对既有 Spec 重算 binding_sha256."""
    return compute_binding_sha256(spec.uniform_schema, dict(spec.uniform_values))


def _plan_semantic_dict(plan: LayerPlanV1) -> dict[str, Any]:
    return {
        "schema_version": plan.schema_version,
        "layers": [layer.to_dict() for layer in plan.layers],
        "reference_sha256": plan.reference_sha256,
        "author_identity": plan.author_identity.to_dict(),
        "observations_ref": plan.observations_ref,
    }


def compute_plan_sha256(
    *,
    schema_version: str,
    layers: tuple[Any, ...],
    reference_sha256: str,
    author_identity: Any,
    observations_ref: str | None,
) -> str:
    """返回 LayerPlan 规范化 JSON 的内容哈希，作为一切引用的唯一身份."""
    canonical = {
        "schema_version": schema_version,
        "layers": [layer.to_dict() for layer in layers],
        "reference_sha256": reference_sha256,
        "author_identity": author_identity.to_dict(),
        "observations_ref": observations_ref,
    }
    return sha256_hex_text(canonical_json(canonical))


def recompute_plan_sha256(plan: LayerPlanV1) -> str:
    """对既有 LayerPlan 重算 plan_sha256."""
    return sha256_hex_text(canonical_json(_plan_semantic_dict(plan)))
