"""Layer 级 direct GLSL 的不可变领域契约。."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

from shaderforge.program_spec import (
    AuthorIdentity,
    CanvasSpec,
    TunableParameter,
    UniformDeclaration,
)
from shaderforge.program_spec.models import LayerRole

if TYPE_CHECKING:
    from shaderforge.uniform_optimization.models import (
        UniformOptimizationProvenanceV1,
    )

LAYERED_SHADER_SPEC_V1_SCHEMA_VERSION = "layered_shader_spec_v1"
LAYER_PATCH_V1_SCHEMA_VERSION = "layer_patch_v1"


def _components_for_model(values: tuple[float, ...]) -> float | list[float]:
    return values[0] if len(values) == 1 else list(values)


def uniform_declaration_to_model_dict(
    declaration: UniformDeclaration,
) -> dict[str, Any]:
    """把 canonical uniform declaration 转为模型 JSON 形状。."""
    return {
        "type": declaration.type,
        "minimum": _components_for_model(declaration.minimum),
        "maximum": _components_for_model(declaration.maximum),
        "default": _components_for_model(declaration.default),
    }


def tunable_parameter_to_model_dict(parameter: TunableParameter) -> dict[str, Any]:
    """把 canonical tunable parameter 转为模型 JSON 形状。."""
    return {
        "path": parameter.path,
        "type": parameter.type,
        "minimum": _components_for_model(parameter.minimum),
        "maximum": _components_for_model(parameter.maximum),
        "step": parameter.step,
    }


@dataclass(frozen=True)
class LayerProgram:
    """模型维护的一层函数体、绑定与可信内容哈希。."""

    layer_id: str
    role: LayerRole
    z_index: int
    glsl_body: str
    uniform_schema: tuple[UniformDeclaration, ...]
    uniform_values: Mapping[str, Any]
    tunable_manifest: tuple[TunableParameter, ...]
    layer_sha256: str

    def semantic_dict(self) -> dict[str, Any]:
        """返回不含自哈希的 canonical 语义字段。."""
        return {
            "layer_id": self.layer_id,
            "role": self.role,
            "z_index": self.z_index,
            "glsl_body": self.glsl_body,
            "uniform_schema": {
                item.name: uniform_declaration_to_model_dict(item)
                for item in sorted(self.uniform_schema, key=lambda item: item.name)
            },
            "uniform_values": {
                name: list(value) if isinstance(value, tuple) else value
                for name, value in sorted(self.uniform_values.items())
            },
            "tunable_manifest": [
                tunable_parameter_to_model_dict(item)
                for item in sorted(self.tunable_manifest, key=lambda item: item.path)
            ],
        }

    def to_dict(self) -> dict[str, Any]:
        """返回包含可信 layer_sha256 的可持久化字典。."""
        return {**self.semantic_dict(), "layer_sha256": self.layer_sha256}


@dataclass(frozen=True)
class LayeredShaderSpecV1:
    """与一个 canonical LayerPlan 一一对应的 Layer 级程序表示。."""

    schema_version: str
    plan_sha256: str
    canvas: CanvasSpec
    layers: tuple[LayerProgram, ...]
    author_identity: AuthorIdentity
    layered_spec_sha256: str
    derivation_provenance: UniformOptimizationProvenanceV1 | None = None

    def to_dict(self) -> dict[str, Any]:
        """返回包含可信身份与哈希的可持久化字典。."""
        payload = {
            "schema_version": self.schema_version,
            "plan_sha256": self.plan_sha256,
            "canvas": self.canvas.to_dict(),
            "layers": [layer.to_dict() for layer in self.layers],
            "author_identity": self.author_identity.to_dict(),
            "layered_spec_sha256": self.layered_spec_sha256,
        }
        if self.derivation_provenance is not None:
            payload["derivation_provenance"] = self.derivation_provenance.to_dict()
        return payload


@dataclass(frozen=True)
class LayerPatchV1:
    """只替换一个既有 Layer 的乐观并发 Patch。."""

    schema_version: str
    base_layered_spec_sha256: str
    target_layer_id: str
    expected_layer_sha256: str
    replacement: LayerProgram

    def to_dict(self) -> dict[str, Any]:
        """返回包含并发保护哈希的可持久化字典。."""
        return {
            "schema_version": self.schema_version,
            "base_layered_spec_sha256": self.base_layered_spec_sha256,
            "target_layer_id": self.target_layer_id,
            "expected_layer_sha256": self.expected_layer_sha256,
            "replacement": self.replacement.to_dict(),
        }
