"""scene_mvp 固定模板生成公共接口."""

from shaderforge.generation.min_template import (
    MAX_MIN_FEATURES,
    MIN_TEMPLATE_FRAGMENT_UNIFORM_VECTORS,
    MIN_TEMPLATE_VERSION,
    WEBGL1_MIN_FRAGMENT_UNIFORM_VECTORS,
    MaterializedMinShader,
    UniformSpec,
    bake_min_uniforms,
    materialize_min_shader,
)

__all__ = [
    "MAX_MIN_FEATURES",
    "MIN_TEMPLATE_FRAGMENT_UNIFORM_VECTORS",
    "MIN_TEMPLATE_VERSION",
    "MaterializedMinShader",
    "UniformSpec",
    "WEBGL1_MIN_FRAGMENT_UNIFORM_VECTORS",
    "bake_min_uniforms",
    "materialize_min_shader",
]
