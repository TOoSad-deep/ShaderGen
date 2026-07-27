"""WebGL1 无贴图 Shader 静态校验."""

from shaderforge.validation.models import (
    ShaderRepairResult,
    ValidationResult,
    ValidationViolation,
)
from shaderforge.validation.program_spec_safety import (
    ProgramSpecSafetyLimits,
    validate_program_spec_safety,
)
from shaderforge.validation.shader_validator import (
    repair_constant_reversed_smoothsteps,
    validate_shader,
)

__all__ = [
    "ProgramSpecSafetyLimits",
    "ShaderRepairResult",
    "ValidationResult",
    "ValidationViolation",
    "repair_constant_reversed_smoothsteps",
    "validate_program_spec_safety",
    "validate_shader",
]
