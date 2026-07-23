"""WebGL1 无贴图 Shader 的快速静态校验器."""

from __future__ import annotations

import re

from shaderforge.contracts.webgl1 import (
    WEBGL1_STATIC_NO_TEXTURE_V1,
    RenderContract,
)
from shaderforge.validation.models import (
    ShaderRepairResult,
    ValidationResult,
    ValidationViolation,
)

COMMENT_PATTERN = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
NUMBER_PATTERN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
SMOOTHSTEP_CONSTANT_PATTERN = re.compile(
    rf"\bsmoothstep\s*\(\s*(?P<edge0>{NUMBER_PATTERN})\s*,"
    rf"\s*(?P<edge1>{NUMBER_PATTERN})\s*,"
)


def _require_supported_contract(contract: RenderContract) -> None:
    """拒绝把 V1 专用规则错误标记为其他运行契约."""
    if contract != WEBGL1_STATIC_NO_TEXTURE_V1:
        raise ValueError(
            "validate_shader 当前只支持 canonical "
            f"{WEBGL1_STATIC_NO_TEXTURE_V1.contract_id} 契约。"
        )


def _without_comments(source: str) -> str:
    """移除注释但保留换行数量，便于错误定位."""

    def replace(match: re.Match[str]) -> str:
        return "\n" * match.group(0).count("\n")

    return COMMENT_PATTERN.sub(replace, source)


def _mask_comments(source: str) -> str:
    """以等长空白遮蔽注释，供确定性源码定位使用."""

    def replace(match: re.Match[str]) -> str:
        return "".join("\n" if char == "\n" else " " for char in match.group(0))

    return COMMENT_PATTERN.sub(replace, source)


def _line_number(source: str, index: int) -> int:
    return source.count("\n", 0, index) + 1


def _add_pattern_violation(
    violations: list[ValidationViolation],
    source: str,
    pattern: str,
    *,
    code: str,
    message: str,
    severity: str = "error",
    flags: int = 0,
) -> None:
    match = re.search(pattern, source, flags)
    if match:
        violations.append(
            ValidationViolation(
                code=code,
                message=message,
                severity=severity,  # type: ignore[arg-type]
                line=_line_number(source, match.start()),
            )
        )


def _required_declarations(source: str, violations: list[ValidationViolation]) -> None:
    required = (
        (
            r"\bprecision\s+mediump\s+float\s*;",
            "missing_precision",
            "缺少 precision mediump float 声明。",
        ),
        (
            r"\bvarying\s+vec2\s+v_uv\s*;",
            "missing_v_uv",
            "缺少 varying vec2 v_uv 声明。",
        ),
        (
            r"\buniform\s+sampler2D\s+u_image\s*;",
            "missing_u_image",
            "缺少兼容声明 uniform sampler2D u_image。",
        ),
        (
            r"\buniform\s+vec2\s+u_resolution\s*;",
            "missing_u_resolution",
            "缺少 uniform vec2 u_resolution 声明。",
        ),
        (
            r"\buniform\s+float\s+u_time\s*;",
            "missing_u_time",
            "缺少兼容声明 uniform float u_time。",
        ),
        (
            r"\bvoid\s+main\s*\(\s*\)",
            "missing_main",
            "缺少 void main() 入口。",
        ),
        (
            r"\bgl_FragColor\s*=",
            "missing_fragment_output",
            "缺少 gl_FragColor 赋值。",
        ),
    )
    for pattern, code, message in required:
        if not re.search(pattern, source):
            violations.append(
                ValidationViolation(code=code, message=message, severity="error")
            )


def _smoothstep_warnings(source: str, violations: list[ValidationViolation]) -> None:
    for match in SMOOTHSTEP_CONSTANT_PATTERN.finditer(source):
        first = float(match.group("edge0"))
        second = float(match.group("edge1"))
        if first >= second:
            violations.append(
                ValidationViolation(
                    code="reversed_smoothstep_edges",
                    message="smoothstep 的常量 edge0 必须小于 edge1。",
                    severity="error",
                    line=_line_number(source, match.start()),
                )
            )


def _smoothstep_call_end(masked_source: str, start: int) -> int | None:
    """返回 smoothstep 调用右括号后一位；字符串中只处理括号平衡."""
    opening = masked_source.find("(", start)
    if opening < 0:
        return None
    depth = 0
    for index in range(opening, len(masked_source)):
        char = masked_source[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def repair_constant_reversed_smoothsteps(source: str) -> ShaderRepairResult | None:
    """按常见反向过渡意图修复常量严格倒序 smoothstep.

    GLSL 对 ``edge0 >= edge1`` 的结果未定义，因此这里不是语义等价证明。
    仅处理 ``edge0 > edge1`` 的完整调用；相等边界继续交给 Validator 和后续
    compile repair。注释中的文本不会被改写。
    """
    masked = _mask_comments(source)
    replacements: list[tuple[int, int, str, int]] = []
    occupied_until = -1
    for match in SMOOTHSTEP_CONSTANT_PATTERN.finditer(masked):
        if match.start() < occupied_until:
            continue
        edge0 = float(match.group("edge0"))
        edge1 = float(match.group("edge1"))
        if edge0 <= edge1:
            continue
        call_end = _smoothstep_call_end(masked, match.start())
        if call_end is None:
            continue
        argument_start = match.end()
        argument_end = call_end - 1
        argument = source[argument_start:argument_end]
        repaired_call = (
            "(1.0 - smoothstep("
            + match.group("edge1")
            + ", "
            + match.group("edge0")
            + ","
            + argument
            + "))"
        )
        replacements.append(
            (
                match.start(),
                call_end,
                repaired_call,
                _line_number(source, match.start()),
            )
        )
        occupied_until = call_end

    if not replacements:
        return None
    repaired = source
    for start, end, value, _line in reversed(replacements):
        repaired = repaired[:start] + value + repaired[end:]
    return ShaderRepairResult(
        source=repaired,
        strategy="constant_reversed_smoothstep_v1",
        repaired_lines=tuple(line for *_prefix, line in replacements),
        replacement_count=len(replacements),
    )


def validate_shader(
    source: str,
    *,
    contract: RenderContract = WEBGL1_STATIC_NO_TEXTURE_V1,
    max_shader_chars: int = 30_000,
) -> ValidationResult:
    """按 V1 WebGL1 无贴图契约静态校验 Fragment Shader."""
    _require_supported_contract(contract)
    violations: list[ValidationViolation] = []
    if not source.strip():
        violations.append(
            ValidationViolation(
                code="empty_source",
                message="Shader 源码不能为空。",
                severity="error",
            )
        )
        return ValidationResult(
            valid=False,
            violations=tuple(violations),
            source_chars=len(source),
            contract_id=contract.contract_id,
        )
    if len(source) > max_shader_chars:
        violations.append(
            ValidationViolation(
                code="source_too_large",
                message=f"Shader 源码超过 {max_shader_chars} 字符上限。",
                severity="error",
            )
        )

    cleaned = _without_comments(source)
    effective = cleaned.lstrip()
    if not effective.startswith("precision mediump float;"):
        violations.append(
            ValidationViolation(
                code="precision_not_first",
                message="第一条有效声明必须是 precision mediump float;。",
                severity="error",
                line=1,
            )
        )
    _required_declarations(cleaned, violations)

    patterns = (
        (r"^\s*#\s*version\b", "version_directive", "WebGL1 禁止 #version。"),
        (r"^\s*#\s*extension\b", "extension_directive", "V1 禁止未声明扩展。"),
        (
            r"\b(?:fwidth|dFdx|dFdy)\s*\(",
            "unsupported_derivative_builtin",
            "V1 禁止依赖标准导数扩展的函数。",
        ),
        (
            r"\b(?:texture2D|textureCube|texture|texelFetch)\s*\(",
            "texture_sampling",
            "无贴图契约禁止任何纹理采样。",
        ),
        (
            r"\bmainImage\s*\(",
            "shadertoy_entry",
            "必须使用 void main()，禁止 mainImage。",
        ),
        (
            r"^\s*(?:flat\s+)?(?:in|out)\s+\w+",
            "webgl2_io",
            "WebGL1 禁止 in/out Shader IO 语法。",
        ),
        (r"\bfragColor\b", "custom_fragment_output", "必须使用 gl_FragColor 输出。"),
        (r"\bwhile\s*\(", "unbounded_loop", "V1 禁止 while 循环。"),
        (r"\bdo\s*\{", "unbounded_loop", "V1 禁止 do/while 循环。"),
        (r"\bfor\s*\(\s*;\s*;", "unbounded_loop", "V1 禁止无条件 for 循环。"),
        (r"/\s*(?:0+(?:\.0*)?|\.0+)\b", "literal_divide_by_zero", "存在字面量除零。"),
        (
            r"\bnormalize\s*\(\s*vec[234]\s*\(\s*0(?:\.0+)?\s*\)\s*\)",
            "normalize_zero",
            "禁止 normalize 零向量。",
        ),
    )
    for pattern, code, message in patterns:
        _add_pattern_violation(
            violations,
            cleaned,
            pattern,
            code=code,
            message=message,
            flags=re.MULTILINE,
        )

    _add_pattern_violation(
        violations,
        cleaned,
        r"\b(?:gl_FragCoord\.[xy]|u_resolution\.[xy])\s*\*\s*(?:gl_FragCoord\.[xy]|u_resolution\.[xy])",
        code="mediump_large_square_risk",
        message="直接平方像素或分辨率坐标可能在 mediump 下溢出。",
        severity="warning",
    )
    _smoothstep_warnings(cleaned, violations)

    errors = [item for item in violations if item.severity == "error"]
    return ValidationResult(
        valid=not errors,
        violations=tuple(violations),
        source_chars=len(source),
        contract_id=contract.contract_id,
    )
