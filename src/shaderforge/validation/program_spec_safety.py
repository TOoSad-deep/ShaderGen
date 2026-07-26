"""ShaderProgramSpecV1 进入渲染前的全量静态安全校验（fail-closed）.

复用 V1 WebGL1 静态校验器的 GLSL 规则，并叠加 ProgramSpec 专属检查：
可信哈希完整性、资源上限、canvas 边界与有界循环。任一 error 即拒绝候选，
不冒泡为未分类异常。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from shaderforge.contracts.webgl1 import WEBGL1_STATIC_NO_TEXTURE_V1
from shaderforge.program_spec.hashing import (
    recompute_binding_sha256,
    recompute_source_sha256,
    recompute_spec_sha256,
)
from shaderforge.program_spec.models import (
    SHADER_PROGRAM_SPEC_V1_SCHEMA_VERSION,
    UNIFORM_COMPONENT_COUNTS,
    ShaderProgramSpecV1,
)
from shaderforge.validation.models import ValidationResult, ValidationViolation
from shaderforge.validation.shader_validator import (
    _without_comments,
    validate_shader,
)

_FOR_HEADER_PATTERN = re.compile(r"\bfor\s*\(([^)]*)\)")
_CANONICAL_FOR_PATTERN = re.compile(
    r"^\s*int\s+(?P<init_name>[A-Za-z_]\w*)\s*=\s*(?P<start>-?\d+)\s*;"
    r"\s*(?P<condition_name>[A-Za-z_]\w*)\s*(?P<operator><=|>=|<|>)"
    r"\s*(?P<bound>-?\d+)\s*;"
    r"\s*(?P<step>.+?)\s*$"
)
_COMPOUND_STEP_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z_]\w*)\s*(?P<operator>\+=|-=)\s*(?P<amount>\d+)$"
)
_FORBIDDEN_PREPROCESSOR_PATTERN = re.compile(
    r"(?m)^\s*#\s*(?:define|undef|include|pragma|line|error)\b"
)


@dataclass(frozen=True)
class ProgramSpecSafetyLimits:
    """ProgramSpec 静态资源上限，默认值对齐 WebGL1 最低保证与 V1 契约."""

    max_source_chars: int = 30_000
    max_uniforms: int = 16
    max_uniform_components: int = 64
    max_tunables: int = 16
    max_canvas_side: int = WEBGL1_STATIC_NO_TEXTURE_V1.max_long_side
    max_loop_iterations: int = 1024


def _loop_step(step: str, variable: str) -> int | None:
    """解析规范整数步长；非规范、零步长或变量不一致返回 None."""
    compact = re.sub(r"\s+", "", step)
    if compact in {f"{variable}++", f"++{variable}"}:
        return 1
    if compact in {f"{variable}--", f"--{variable}"}:
        return -1
    match = _COMPOUND_STEP_PATTERN.fullmatch(compact)
    if match is None or match.group("name") != variable:
        return None
    amount = int(match.group("amount"))
    if amount == 0:
        return None
    return amount if match.group("operator") == "+=" else -amount


def _loop_trip_count(start: int, bound: int, operator: str, step: int) -> int | None:
    """计算规范整数循环次数；方向错误返回 None."""
    if operator in {"<", "<="}:
        if step <= 0:
            return None
        if start > bound or (operator == "<" and start == bound):
            return 0
        distance = bound - start
        return (
            (distance + step - 1) // step if operator == "<" else distance // step + 1
        )
    if step >= 0:
        return None
    positive_step = -step
    if start < bound or (operator == ">" and start == bound):
        return 0
    distance = start - bound
    return (
        (distance + positive_step - 1) // positive_step
        if operator == ">"
        else distance // positive_step + 1
    )


def _bounded_loop_violations(
    source: str, *, max_loop_iterations: int
) -> list[ValidationViolation]:
    """只允许可证明次数的规范整数 for，并限制最大迭代数.

    宏能把 ``LOOP`` 展开成 ``for``，从而绕过只扫描原始源码 token 的检查。
    ProgramSpec 因此直接拒绝会创建/改写 token 的预处理指令；条件编译和
    ``#version`` 仍由底层 WebGL1 validator 按既有契约处理。
    """
    violations: list[ValidationViolation] = []
    for directive in _FORBIDDEN_PREPROCESSOR_PATTERN.finditer(source):
        violations.append(
            ValidationViolation(
                code="forbidden_preprocessor",
                message="ProgramSpec 禁止宏定义、包含或改写源码 token 的预处理指令。",
                severity="error",
                line=source.count("\n", 0, directive.start()) + 1,
            )
        )
    for match in _FOR_HEADER_PATTERN.finditer(source):
        header = _CANONICAL_FOR_PATTERN.fullmatch(match.group(1))
        line = source.count("\n", 0, match.start()) + 1
        if header is None:
            violations.append(
                ValidationViolation(
                    code="unbounded_loop",
                    message="for 循环必须使用规范整数初值、常量边界和静态步长。",
                    severity="error",
                    line=line,
                )
            )
            continue
        variable = header.group("init_name")
        if header.group("condition_name") != variable:
            trip_count = None
        else:
            step = _loop_step(header.group("step"), variable)
            trip_count = (
                None
                if step is None
                else _loop_trip_count(
                    int(header.group("start")),
                    int(header.group("bound")),
                    header.group("operator"),
                    step,
                )
            )
        if trip_count is None:
            violations.append(
                ValidationViolation(
                    code="unbounded_loop",
                    message="for 循环变量或步长方向无法证明会终止。",
                    severity="error",
                    line=line,
                )
            )
        elif trip_count > max_loop_iterations:
            violations.append(
                ValidationViolation(
                    code="loop_iteration_limit",
                    message=f"for 循环迭代数超过 {max_loop_iterations} 上限。",
                    severity="error",
                    line=line,
                )
            )
    return violations


def validate_program_spec_safety(
    spec: ShaderProgramSpecV1,
    *,
    limits: ProgramSpecSafetyLimits | None = None,
    validate_source: Callable[..., ValidationResult] = validate_shader,
) -> ValidationResult:
    """对 Spec 执行静态安全校验，返回与 V1 一致的 ValidationResult.

    检查顺序：契约身份与哈希完整性、资源上限、GLSL 静态规则、有界循环。
    ``validate_source`` 可注入真实 V1 校验器之外的实现以便测试。
    """
    effective_limits = limits or ProgramSpecSafetyLimits()
    violations: list[ValidationViolation] = []

    if spec.schema_version != SHADER_PROGRAM_SPEC_V1_SCHEMA_VERSION:
        violations.append(
            ValidationViolation(
                code="invalid_schema_version",
                message=f"schema_version 必须是 {SHADER_PROGRAM_SPEC_V1_SCHEMA_VERSION}。",
                severity="error",
            )
        )
    if spec.renderer_contract_id != WEBGL1_STATIC_NO_TEXTURE_V1.contract_id:
        violations.append(
            ValidationViolation(
                code="unsupported_renderer_contract",
                message="renderer_contract_id 只支持 canonical "
                f"{WEBGL1_STATIC_NO_TEXTURE_V1.contract_id}。",
                severity="error",
            )
        )

    if recompute_source_sha256(spec) != spec.source_sha256:
        violations.append(
            ValidationViolation(
                code="source_hash_mismatch",
                message="source_sha256 与源码内容重算不匹配。",
                severity="error",
            )
        )
    if recompute_binding_sha256(spec) != spec.binding_sha256:
        violations.append(
            ValidationViolation(
                code="binding_hash_mismatch",
                message="binding_sha256 与 uniform 绑定内容重算不匹配。",
                severity="error",
            )
        )
    if recompute_spec_sha256(spec) != spec.spec_sha256:
        violations.append(
            ValidationViolation(
                code="spec_hash_mismatch",
                message="spec_sha256 与语义字段内容重算不匹配。",
                severity="error",
            )
        )

    if len(spec.fragment_source) > effective_limits.max_source_chars:
        violations.append(
            ValidationViolation(
                code="source_too_large",
                message=f"fragment_source 超过 {effective_limits.max_source_chars} 字符上限。",
                severity="error",
            )
        )
    if len(spec.uniform_schema) > effective_limits.max_uniforms:
        violations.append(
            ValidationViolation(
                code="too_many_uniforms",
                message=f"uniform 数量超过 {effective_limits.max_uniforms} 上限。",
                severity="error",
            )
        )
    total_components = sum(
        UNIFORM_COMPONENT_COUNTS[item.type] for item in spec.uniform_schema
    )
    if total_components > effective_limits.max_uniform_components:
        violations.append(
            ValidationViolation(
                code="too_many_uniform_components",
                message="uniform 总分量超过 "
                f"{effective_limits.max_uniform_components} 上限。",
                severity="error",
            )
        )
    if len(spec.tunable_manifest) > effective_limits.max_tunables:
        violations.append(
            ValidationViolation(
                code="too_many_tunables",
                message=f"tunable 参数数量超过 {effective_limits.max_tunables} 上限。",
                severity="error",
            )
        )
    long_side = max(spec.canvas.width, spec.canvas.height)
    if long_side > effective_limits.max_canvas_side:
        violations.append(
            ValidationViolation(
                code="canvas_too_large",
                message=f"canvas 长边超过 {effective_limits.max_canvas_side} 像素上限。",
                severity="error",
            )
        )

    source_result = validate_source(
        spec.fragment_source,
        contract=WEBGL1_STATIC_NO_TEXTURE_V1,
        max_shader_chars=effective_limits.max_source_chars,
    )
    violations.extend(source_result.violations)
    violations.extend(
        _bounded_loop_violations(
            _without_comments(spec.fragment_source),
            max_loop_iterations=effective_limits.max_loop_iterations,
        )
    )

    errors = [item for item in violations if item.severity == "error"]
    return ValidationResult(
        valid=not errors,
        violations=tuple(violations),
        source_chars=len(spec.fragment_source),
        contract_id=spec.renderer_contract_id,
    )
