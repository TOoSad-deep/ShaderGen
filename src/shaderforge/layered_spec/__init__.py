"""Layer 级 direct GLSL 的纯确定性领域包。."""

from shaderforge.layered_spec.compiler import (
    LAYERED_COMPILER_VERSION,
    compile_layered_shader,
)
from shaderforge.layered_spec.hashing import (
    compute_layer_sha256,
    compute_layered_spec_sha256,
    recompute_layer_sha256,
    recompute_layered_spec_sha256,
)
from shaderforge.layered_spec.models import (
    LAYER_PATCH_V1_SCHEMA_VERSION,
    LAYERED_SHADER_SPEC_V1_SCHEMA_VERSION,
    LayeredShaderSpecV1,
    LayerPatchV1,
    LayerProgram,
)
from shaderforge.layered_spec.parsing import (
    LayeredSpecError,
    build_layer_patch,
    build_layered_shader_spec,
    parse_layer_patch,
)
from shaderforge.layered_spec.patching import apply_layer_patch

__all__ = [
    "LAYERED_SHADER_SPEC_V1_SCHEMA_VERSION",
    "LAYER_PATCH_V1_SCHEMA_VERSION",
    "LAYERED_COMPILER_VERSION",
    "LayerPatchV1",
    "LayerProgram",
    "LayeredShaderSpecV1",
    "LayeredSpecError",
    "apply_layer_patch",
    "build_layer_patch",
    "build_layered_shader_spec",
    "compile_layered_shader",
    "compute_layer_sha256",
    "compute_layered_spec_sha256",
    "parse_layer_patch",
    "recompute_layer_sha256",
    "recompute_layered_spec_sha256",
]
