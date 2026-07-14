"""严格解析 PNG 转 Shader V1 的三个模型角色输出."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError

from agent.app.contracts.png_to_shader_v1 import (
    AuthorMode,
    ShaderAuthorResult,
    VisualAnalysis,
    VisualReview,
)
from shaderforge.contracts import ProblemDomain

MAX_STRUCTURED_OUTPUT_CHARS = 100_000
_JSON_FENCE_RE = re.compile(r"\A\s*```json\s*\n(?P<body>.*)\n```\s*\Z", re.DOTALL)
_VISUAL_DOMAINS = {
    domain for domain in ProblemDomain if domain != ProblemDomain.RUNTIME_COMPILE
}
_ROI_PURPOSE_PATH_RE = re.compile(
    r"^\$\.'regions_of_interest'\.'(?P<index>\d+)'\.'purpose'$"
)
_ROI_PURPOSE_ALIASES = {
    "background": "protection",
    "background_border": "protection",
    "boundary": "edge",
    "color_field": "color",
    "colour": "color",
    "contour": "edge",
    "foreground": "geometry",
    "gloss": "highlight",
    "occlusion": "shadow",
    "outline": "edge",
    "preserve": "protection",
    "protected": "protection",
    "reflection": "highlight",
    "rim": "edge",
    "shape": "geometry",
    "silhouette": "geometry",
    "specular": "highlight",
    "subject": "geometry",
    "tone": "color",
    "背景": "protection",
    "保护": "protection",
    "几何": "geometry",
    "反光": "highlight",
    "轮廓": "edge",
    "阴影": "shadow",
    "颜色": "color",
    "高光": "highlight",
}
_INITIAL_IDENTITY_BINDINGS: dict[str, Any] = {
    "author_version": "shader_author_initial_v1_1",
    "mode": AuthorMode.INITIAL.value,
    "base_candidate_id": None,
    "changed_problem_domain": "initial_build",
}
_INITIAL_REPAIRABLE_BINDINGS: dict[str, Any] = {
    "changed_parameters": [],
    "protected_regions": [],
}
_INITIAL_REPAIRABLE_BINDING_PATHS = {
    f"$.{field_name}" for field_name in _INITIAL_REPAIRABLE_BINDINGS
}
_ModelT = TypeVar("_ModelT", bound=BaseModel)


@dataclass(frozen=True)
class StructuredOutputIssue:
    """一个可安全进入修复 Prompt 的结构化错误."""

    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        """返回不含模型原始输出的普通字典."""
        return {"code": self.code, "path": self.path, "message": self.message}


class PngToShaderParseError(ValueError):
    """表示输出无法满足严格 JSON 或角色契约."""

    def __init__(self, issues: list[StructuredOutputIssue], *, raw_text: str) -> None:
        """只保留安全错误与原始输出哈希."""
        self.issues = tuple(issues)
        self.raw_sha256 = sha256(raw_text.encode("utf-8")).hexdigest()
        codes = ",".join(dict.fromkeys(issue.code for issue in issues))
        super().__init__(f"结构化输出不合法：{codes}。")

    @property
    def error_codes(self) -> tuple[str, ...]:
        """返回去重且稳定排序前的错误码序列."""
        return tuple(dict.fromkeys(issue.code for issue in self.issues))


def _duplicate_key_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    duplicates: list[str] = []
    for key, value in pairs:
        if key in result:
            duplicates.append(key)
        result[key] = value
    if duplicates:
        names = ", ".join(sorted(set(duplicates)))
        raise ValueError(f"duplicate keys: {names}")
    return result


def _reject_non_finite_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _unwrap_json(text: str) -> str:
    stripped = text.strip()
    if len(stripped) > MAX_STRUCTURED_OUTPUT_CHARS:
        raise PngToShaderParseError(
            [
                StructuredOutputIssue(
                    code="output_too_large",
                    path="$",
                    message=f"输出超过 {MAX_STRUCTURED_OUTPUT_CHARS} 字符上限",
                )
            ],
            raw_text=text,
        )
    if stripped.startswith("```"):
        match = _JSON_FENCE_RE.fullmatch(stripped)
        if match is None:
            raise PngToShaderParseError(
                [
                    StructuredOutputIssue(
                        code="unexpected_wrapper",
                        path="$",
                        message="只允许单个完整的 ```json fenced object",
                    )
                ],
                raw_text=text,
            )
        body = match.group("body")
        if "```" in body:
            raise PngToShaderParseError(
                [
                    StructuredOutputIssue(
                        code="unexpected_wrapper",
                        path="$",
                        message="只允许一个 JSON fenced code block",
                    )
                ],
                raw_text=text,
            )
        return body.strip()
    return stripped


def _load_json_object(text: str) -> dict[str, Any]:
    payload = _unwrap_json(text)
    try:
        value = json.loads(
            payload,
            object_pairs_hook=_duplicate_key_object,
            parse_constant=_reject_non_finite_constant,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        message = str(exc)
        if "duplicate keys:" in message:
            code = "duplicate_key"
        elif "non-finite JSON constant:" in message:
            code = "non_finite_number"
        else:
            code = "invalid_json"
        raise PngToShaderParseError(
            [StructuredOutputIssue(code=code, path="$", message=message[:300])],
            raw_text=text,
        ) from exc
    if not isinstance(value, dict):
        raise PngToShaderParseError(
            [
                StructuredOutputIssue(
                    code="not_json_object",
                    path="$",
                    message="顶层必须是 JSON object",
                )
            ],
            raw_text=text,
        )
    return value


def _validation_code(error: Mapping[str, Any]) -> str:
    message = str(error.get("msg", ""))
    error_type = str(error.get("type", "schema_validation"))
    location = error.get("loc", ())
    field_name = str(location[-1]).casefold() if location else ""
    if "完整 GLSL" in message or "纹理采样" in message:
        return "role_violation"
    if error_type == "extra_forbidden" and field_name in {
        "glsl",
        "shader",
        "shader_code",
    }:
        return "role_violation"
    if error_type == "extra_forbidden":
        return "unknown_field"
    if error_type == "missing":
        return "missing_field"
    if error_type == "literal_error":
        return "invalid_literal"
    return "schema_validation"


def _validation_path(location: tuple[int | str, ...]) -> str:
    if not location:
        return "$"
    return "$.'" + "'.'".join(str(item) for item in location) + "'"


def _validate_model(text: str, model_type: type[_ModelT]) -> _ModelT:
    payload = _load_json_object(text)
    try:
        # JSON array 在 Python 中必然是 list；Pydantic 需要把它规范化为 tuple。
        # 数字、布尔和整数字段的严格性由字段级 strict 约束负责。
        return model_type.model_validate(payload)
    except ValidationError as exc:
        issues = [
            StructuredOutputIssue(
                code=_validation_code(error),
                path=_validation_path(error["loc"]),
                message=str(error["msg"])[:300],
            )
            for error in exc.errors(include_url=False, include_context=False)
        ]
        raise PngToShaderParseError(issues, raw_text=text) from exc


def _binding_error(text: str, path: str, message: str) -> PngToShaderParseError:
    return PngToShaderParseError(
        [StructuredOutputIssue(code="binding_mismatch", path=path, message=message)],
        raw_text=text,
    )


def parse_visual_analysis(
    text: str,
    *,
    expected_version: str = "visual_analysis_v1_2",
) -> VisualAnalysis:
    """解析 Analyst 输出并校验版本绑定."""
    result = _validate_model(text, VisualAnalysis)
    if result.analysis_version != expected_version:
        raise _binding_error(
            text,
            "$.analysis_version",
            f"期望 {expected_version}，实际 {result.analysis_version}",
        )
    return result


def repair_visual_analysis_roi_purposes(
    text: str,
    error: PngToShaderParseError,
    *,
    expected_version: str = "visual_analysis_v1_2",
) -> tuple[VisualAnalysis, dict[str, Any]] | None:
    """只修复已知 ROI purpose 别名，其余错误继续交给严格修复路径.

    这个入口不会放宽 ``parse_visual_analysis`` 的公共契约。只有当全部错误都
    精确指向 ``regions_of_interest[*].purpose``，且每个值都命中显式别名表时，
    才在本地重验整份对象；任何未知值、额外错误或重验失败都会返回 ``None``。
    """
    if not error.issues:
        return None
    indexed_paths: list[tuple[int, str]] = []
    for issue in error.issues:
        match = _ROI_PURPOSE_PATH_RE.fullmatch(issue.path)
        if issue.code != "invalid_literal" or match is None:
            return None
        indexed_paths.append((int(match.group("index")), issue.path))

    try:
        payload = _load_json_object(text)
        regions = payload["regions_of_interest"]
        if not isinstance(regions, list):
            return None
        repaired_paths: list[str] = []
        for index, path in indexed_paths:
            region = regions[index]
            if not isinstance(region, dict):
                return None
            raw_purpose = region.get("purpose")
            if not isinstance(raw_purpose, str):
                return None
            alias = raw_purpose.strip().casefold().replace("-", "_").replace(" ", "_")
            normalized = _ROI_PURPOSE_ALIASES.get(alias)
            if normalized is None:
                return None
            region["purpose"] = normalized
            repaired_paths.append(path)
        repaired_text = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        value = parse_visual_analysis(
            repaired_text,
            expected_version=expected_version,
        )
    except (KeyError, IndexError, TypeError, PngToShaderParseError):
        return None
    return value, {
        "strategy": "visual_analysis_roi_purpose_alias_v1",
        "repaired_paths": repaired_paths,
        "source_error_codes": list(error.error_codes),
    }


def _validate_initial_binding(text: str, result: ShaderAuthorResult) -> None:
    if result.author_version != "shader_author_initial_v1_1":
        raise _binding_error(text, "$.author_version", "initial Prompt 版本不匹配")
    if result.mode != AuthorMode.INITIAL:
        raise _binding_error(text, "$.mode", "initial Prompt 只能返回 initial")
    if result.base_candidate_id is not None:
        raise _binding_error(text, "$.base_candidate_id", "initial 不得绑定已有候选")
    if result.changed_problem_domain != "initial_build":
        raise _binding_error(
            text, "$.changed_problem_domain", "initial 必须使用 initial_build"
        )
    if result.changed_parameters or result.protected_regions:
        raise _binding_error(
            text,
            "$.changed_parameters",
            "initial 的 changed_parameters 和 protected_regions 必须为空",
        )


def _validate_compile_binding(text: str, result: ShaderAuthorResult) -> None:
    if result.author_version != "shader_author_compile_repair_v1_1":
        raise _binding_error(
            text, "$.author_version", "compile repair Prompt 版本不匹配"
        )
    if result.mode != AuthorMode.COMPILE_REPAIR:
        raise _binding_error(text, "$.mode", "compile repair 模式不匹配")
    if result.changed_problem_domain != ProblemDomain.RUNTIME_COMPILE.value:
        raise _binding_error(
            text, "$.changed_problem_domain", "compile repair 只能修改 runtime_compile"
        )


def _validate_refine_binding(
    text: str,
    result: ShaderAuthorResult,
    *,
    expected_base_candidate_id: str | None,
    expected_problem_domain: ProblemDomain | None,
) -> None:
    if result.author_version != "shader_author_visual_refine_v1":
        raise _binding_error(
            text, "$.author_version", "visual refine Prompt 版本不匹配"
        )
    if result.mode != AuthorMode.VISUAL_REFINE:
        raise _binding_error(text, "$.mode", "visual refine 模式不匹配")
    if (
        not expected_base_candidate_id
        or result.base_candidate_id != expected_base_candidate_id
    ):
        raise _binding_error(
            text, "$.base_candidate_id", "visual refine 未绑定 current_best candidate"
        )
    if (
        expected_problem_domain is None
        or expected_problem_domain not in _VISUAL_DOMAINS
    ):
        raise _binding_error(
            text,
            "$.changed_problem_domain",
            "visual refine 必须提供非 runtime_compile 问题域",
        )
    if result.changed_problem_domain != expected_problem_domain.value:
        raise _binding_error(
            text,
            "$.changed_problem_domain",
            "visual refine 修改域必须等于 Critic primary_problem_domain",
        )


def validate_compile_repair_scope(
    previous: ShaderAuthorResult,
    repaired: ShaderAuthorResult,
    *,
    diagnostics: str,
    expected_protected_regions: tuple[str, ...] = (),
) -> None:
    """确定性阻止 compile repair 改写无关视觉参数.

    该检查能约束 manifest、图层和保护区，但不能证明任意 GLSL 的语义等价；
    M3 仍需 Renderer 和 Oracle 判断视觉是否退化。
    """
    issues: list[StructuredOutputIssue] = []
    if repaired.implemented_layers != previous.implemented_layers:
        issues.append(
            StructuredOutputIssue(
                code="compile_scope_violation",
                path="$.implemented_layers",
                message="compile repair 必须保持视觉层列表和顺序",
            )
        )

    expected_protected = set(expected_protected_regions) | set(
        previous.protected_regions
    )
    if not expected_protected.issubset(repaired.protected_regions):
        issues.append(
            StructuredOutputIssue(
                code="compile_scope_violation",
                path="$.protected_regions",
                message="compile repair 丢失了已有保护区域",
            )
        )

    before = {item.name: item for item in previous.parameter_manifest}
    after = {item.name: item for item in repaired.parameter_manifest}
    if set(before) != set(after):
        issues.append(
            StructuredOutputIssue(
                code="compile_scope_violation",
                path="$.parameter_manifest",
                message="compile repair 不得新增或删除视觉参数",
            )
        )

    diagnostic_text = diagnostics.casefold()
    changed = set(repaired.changed_parameters)
    if changed - set(after):
        issues.append(
            StructuredOutputIssue(
                code="compile_scope_violation",
                path="$.changed_parameters",
                message="changed_parameters 必须引用 parameter_manifest 中的真实名称",
            )
        )

    for name in sorted(set(before) & set(after)):
        old = before[name]
        new = after[name]
        if (
            old.semantic_role != new.semantic_role
            or old.problem_domain != new.problem_domain
            or old.safe_range != new.safe_range
            or old.affected_regions != new.affected_regions
        ):
            issues.append(
                StructuredOutputIssue(
                    code="compile_scope_violation",
                    path=f"$.parameter_manifest.{name}",
                    message="compile repair 不得改写参数语义元数据",
                )
            )
        if old.current_value == new.current_value:
            continue
        if name not in changed:
            issues.append(
                StructuredOutputIssue(
                    code="compile_scope_violation",
                    path=f"$.parameter_manifest.{name}.current_value",
                    message="参数值变化必须在 changed_parameters 中声明",
                )
            )
        if set(old.affected_regions) & expected_protected:
            issues.append(
                StructuredOutputIssue(
                    code="compile_scope_violation",
                    path=f"$.parameter_manifest.{name}.current_value",
                    message="compile repair 改动了保护区域参数",
                )
            )
        if name.casefold() not in diagnostic_text:
            issues.append(
                StructuredOutputIssue(
                    code="compile_scope_violation",
                    path=f"$.changed_parameters.{name}",
                    message="视觉参数变化未被真实编译诊断直接支持",
                )
            )

    if issues:
        raise PngToShaderParseError(issues, raw_text=repaired.model_dump_json())


def parse_shader_author_result(
    text: str,
    *,
    expected_mode: AuthorMode,
    expected_base_candidate_id: str | None = None,
    expected_problem_domain: ProblemDomain | None = None,
    previous_result: ShaderAuthorResult | None = None,
    compile_diagnostics: str = "",
    expected_protected_regions: tuple[str, ...] = (),
) -> ShaderAuthorResult:
    """解析 Author 输出并执行模式绑定和 compile scope guard."""
    result = _validate_model(text, ShaderAuthorResult)
    if expected_mode == AuthorMode.INITIAL:
        _validate_initial_binding(text, result)
    elif expected_mode == AuthorMode.COMPILE_REPAIR:
        _validate_compile_binding(text, result)
        if previous_result is None:
            raise _binding_error(
                text, "$.parameter_manifest", "compile repair 缺少上一候选清单"
            )
        try:
            validate_compile_repair_scope(
                previous_result,
                result,
                diagnostics=compile_diagnostics,
                expected_protected_regions=expected_protected_regions,
            )
        except PngToShaderParseError as exc:
            raise PngToShaderParseError(list(exc.issues), raw_text=text) from exc
    elif expected_mode == AuthorMode.VISUAL_REFINE:
        _validate_refine_binding(
            text,
            result,
            expected_base_candidate_id=expected_base_candidate_id,
            expected_problem_domain=expected_problem_domain,
        )
        if not set(expected_protected_regions).issubset(result.protected_regions):
            raise _binding_error(
                text, "$.protected_regions", "visual refine 未继承 Critic 保护区域"
            )
    else:  # pragma: no cover - AuthorMode 已封闭
        raise ValueError(f"不支持的 Author mode：{expected_mode}")
    return result


def repair_shader_author_initial_bindings(
    text: str,
    error: PngToShaderParseError,
) -> tuple[ShaderAuthorResult, dict[str, Any]] | None:
    """只归一化 Initial Author 的契约固定元数据并整份重验.

    只有 Initial 身份字段原本已全部匹配，且全部错误都来自两个空列表绑定时
    才允许进入；schema、角色边界或任何未知路径错误继续走严格修复流程。
    """
    if not error.issues or any(
        issue.code != "binding_mismatch"
        or issue.path not in _INITIAL_REPAIRABLE_BINDING_PATHS
        for issue in error.issues
    ):
        return None
    try:
        payload = _load_json_object(text)
        if any(
            payload.get(field_name) != expected
            for field_name, expected in _INITIAL_IDENTITY_BINDINGS.items()
        ):
            return None
        repaired_paths: list[str] = []
        for field_name, expected in _INITIAL_REPAIRABLE_BINDINGS.items():
            if payload.get(field_name) == expected:
                continue
            payload[field_name] = (
                list(expected) if isinstance(expected, list) else expected
            )
            repaired_paths.append(f"$.{field_name}")
        if not repaired_paths:
            return None
        repaired_text = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        value = parse_shader_author_result(
            repaired_text,
            expected_mode=AuthorMode.INITIAL,
        )
    except (PngToShaderParseError, TypeError):
        return None
    return value, {
        "strategy": "shader_author_initial_fixed_bindings_v1",
        "repaired_paths": repaired_paths,
        "source_error_codes": list(error.error_codes),
        "source_error_paths": [issue.path for issue in error.issues],
    }


def parse_visual_review(
    text: str,
    *,
    expected_candidate_id: str,
    expected_version: str = "visual_critic_v1",
) -> VisualReview:
    """解析 Critic 输出并校验候选与 Prompt 版本绑定."""
    result = _validate_model(text, VisualReview)
    if result.review_version != expected_version:
        raise _binding_error(text, "$.review_version", "Critic Prompt 版本不匹配")
    if result.candidate_id != expected_candidate_id:
        raise _binding_error(text, "$.candidate_id", "Critic 输出未绑定当前候选")
    return result


def json_schema_for(model_type: type[BaseModel]) -> dict[str, Any]:
    """返回修复调用使用的确定性 JSON Schema."""
    return model_type.model_json_schema(mode="validation")


def parser_for_author(
    *,
    expected_mode: AuthorMode,
    expected_base_candidate_id: str | None = None,
    expected_problem_domain: ProblemDomain | None = None,
    previous_result: ShaderAuthorResult | None = None,
    compile_diagnostics: str = "",
    expected_protected_regions: tuple[str, ...] = (),
) -> Callable[[str], ShaderAuthorResult]:
    """绑定一次 Author 调用所需的全部确定性上下文."""

    def parse(text: str) -> ShaderAuthorResult:
        return parse_shader_author_result(
            text,
            expected_mode=expected_mode,
            expected_base_candidate_id=expected_base_candidate_id,
            expected_problem_domain=expected_problem_domain,
            previous_result=previous_result,
            compile_diagnostics=compile_diagnostics,
            expected_protected_regions=expected_protected_regions,
        )

    return parse
