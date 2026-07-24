"""ShaderGraph Model Author 的纯严格 JSON Parser."""

from __future__ import annotations

import json
from typing import Any

from pydantic import TypeAdapter, ValidationError

from agent.app.contracts.shader_graph_author import ShaderGraphAuthorPatch
from shaderforge.dsl import ShaderDocument

_PATCH_ADAPTER: TypeAdapter[ShaderGraphAuthorPatch] = TypeAdapter(
    ShaderGraphAuthorPatch
)

_DOCUMENT_MAX_CHARS = 100_000
_PATCH_MAX_CHARS = 20_000


class ShaderGraphAuthorParseError(ValueError):
    """表示模型输出不是当前节点允许的完整 JSON 值."""

    def __init__(
        self,
        code: str,
        *,
        details: tuple[dict[str, str], ...] = (),
    ) -> None:
        """保留稳定错误码和脱敏校验位置，不泄露原始值."""
        self.code = code
        self.details = details
        super().__init__(code)


def _validation_details(
    error: ValidationError,
) -> tuple[dict[str, str], ...]:
    """提取 repair 可消费的字段位置、类型和安全消息，不包含 input."""
    return tuple(
        {
            "location": ".".join(str(part) for part in item["loc"]),
            "type": str(item["type"]),
            "message": str(item["msg"])[:240],
        }
        for item in error.errors(include_input=False, include_url=False)[:12]
    )


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"JSON 不允许非有限数：{value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"JSON key 重复：{key}")
        value[key] = item
    return value


def _assert_strict_json(text: str, *, max_chars: int) -> None:
    if len(text) > max_chars:
        raise ValueError("JSON 输出超过字符上限。")
    json.loads(
        text,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_non_finite,
    )


def parse_shader_graph_document(
    text: str,
    *,
    expected_width: int,
    expected_height: int,
) -> ShaderDocument:
    """解析完整 ShaderDocument，并固定参考图画布，禁止模型改变维度."""
    try:
        _assert_strict_json(text, max_chars=_DOCUMENT_MAX_CHARS)
    except ValueError as exc:
        raise ShaderGraphAuthorParseError("invalid_shader_graph_document_json") from exc
    try:
        document = ShaderDocument.model_validate_json(text, strict=True)
    except ValidationError as exc:
        raise ShaderGraphAuthorParseError(
            "invalid_shader_graph_document_json",
            details=_validation_details(exc),
        ) from exc
    if (
        document.canvas.width != expected_width
        or document.canvas.height != expected_height
    ):
        raise ShaderGraphAuthorParseError("shader_graph_canvas_mismatch")
    return document


def parse_shader_graph_author_patch(text: str) -> ShaderGraphAuthorPatch:
    """解析恰好一个绑定 base 哈希的 typed layer patch 对象."""
    try:
        _assert_strict_json(text, max_chars=_PATCH_MAX_CHARS)
    except ValueError as exc:
        raise ShaderGraphAuthorParseError(
            "invalid_shader_graph_author_patch_json"
        ) from exc
    try:
        return _PATCH_ADAPTER.validate_json(text, strict=True)
    except ValidationError as exc:
        raise ShaderGraphAuthorParseError(
            "invalid_shader_graph_author_patch_json",
            details=_validation_details(exc),
        ) from exc


def shader_graph_document_json_schema() -> dict[str, object]:
    """返回 Initial Prompt/结构修复使用的版本化严格 Schema."""
    return ShaderDocument.model_json_schema(mode="validation")


def shader_graph_author_patch_json_schema() -> dict[str, object]:
    """返回 Refine Prompt/结构修复使用的版本化严格 Schema."""
    return _PATCH_ADAPTER.json_schema(mode="validation")


__all__ = [
    "ShaderGraphAuthorParseError",
    "parse_shader_graph_author_patch",
    "parse_shader_graph_document",
    "shader_graph_author_patch_json_schema",
    "shader_graph_document_json_schema",
]
