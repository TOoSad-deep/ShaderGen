"""确定性 Shader seed 生成公共接口."""

from shaderforge.generation.measurement_affine import (
    MEASUREMENT_AFFINE_SEED_VERSION,
    MeasurementAffineSeed,
    MeasurementSeedProvenance,
    build_measurement_affine_seed,
)
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
    "MEASUREMENT_AFFINE_SEED_VERSION",
    "MeasurementAffineSeed",
    "MeasurementSeedProvenance",
    "MaterializedMinShader",
    "UniformSpec",
    "WEBGL1_MIN_FRAGMENT_UNIFORM_VECTORS",
    "bake_min_uniforms",
    "build_measurement_affine_seed",
    "materialize_min_shader",
]
