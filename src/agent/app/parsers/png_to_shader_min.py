"""scene_mvp Model Author 的纯严格 JSON Parser。."""

from __future__ import annotations

import json
from typing import Any

from pydantic import TypeAdapter, ValidationError

from agent.app.contracts.png_to_shader_min import MinAuthorPatch
from shaderforge.public import MinScene

_PATCH_ADAPTER: TypeAdapter[MinAuthorPatch] = TypeAdapter(MinAuthorPatch)


class MinAuthorParseError(ValueError):
    """表示模型输出不是当前节点允许的完整 JSON 值。."""

    def __init__(self, code: str) -> None:
        """只保留稳定错误码，不泄露原始模型输出。."""
        self.code = code
        super().__init__(code)


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


def parse_min_scene(
    text: str,
    *,
    expected_width: int,
    expected_height: int,
) -> MinScene:
    """解析完整 MinScene，并固定参考图画布，禁止模型改变维度。."""
    try:
        _assert_strict_json(text, max_chars=100_000)
        scene = MinScene.model_validate_json(text, strict=True)
    except (ValidationError, ValueError) as exc:
        raise MinAuthorParseError("invalid_min_scene_json") from exc
    if scene.canvas.width != expected_width or scene.canvas.height != expected_height:
        raise MinAuthorParseError("scene_canvas_mismatch")
    return scene


def parse_min_author_patch(text: str) -> MinAuthorPatch:
    """解析恰好一个 path/operation/value 白名单 patch 对象。."""
    try:
        _assert_strict_json(text, max_chars=20_000)
        return _PATCH_ADAPTER.validate_json(text, strict=True)
    except (ValidationError, ValueError) as exc:
        raise MinAuthorParseError("invalid_min_author_patch_json") from exc


def min_author_patch_json_schema() -> dict[str, object]:
    """返回 Refine Prompt/结构修复使用的版本化严格 Schema。."""
    return _PATCH_ADAPTER.json_schema(mode="validation")


__all__ = [
    "MinAuthorParseError",
    "min_author_patch_json_schema",
    "parse_min_author_patch",
    "parse_min_scene",
]
