"""VisualInterpretationV2 的严格 JSON Parser。."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from shaderforge.intent.ir import VisualInterpretationV2

MAX_VISUAL_INTERPRETATION_BYTES = 256_000


class VisualInterpretationParseError(ValueError):
    """表示模型输出不是唯一、严格的 VisualInterpretationV2 JSON。."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise VisualInterpretationParseError(f"JSON 字段不得重复：{key}。")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise VisualInterpretationParseError(f"JSON 不允许非有限数值：{value}。")


def _unwrap_single_json_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 3 or lines[-1].strip() != "```":
        raise VisualInterpretationParseError("JSON code fence 未闭合。")
    if lines[0].strip() not in {"```", "```json", "```JSON"}:
        raise VisualInterpretationParseError("只允许单一 JSON code fence。")
    body = "\n".join(lines[1:-1]).strip()
    if "```" in body:
        raise VisualInterpretationParseError("只允许单一 JSON code fence。")
    return body


def parse_visual_interpretation_v2(text: str) -> VisualInterpretationV2:
    """解析唯一 JSON object；重复 key、未知字段和确定性字段均 fail closed。."""
    if not isinstance(text, str) or not text.strip():
        raise VisualInterpretationParseError("VisualInterpretation 输出不能为空。")
    if len(text.encode("utf-8")) > MAX_VISUAL_INTERPRETATION_BYTES:
        raise VisualInterpretationParseError("VisualInterpretation 输出超过大小上限。")
    payload = _unwrap_single_json_fence(text)
    try:
        raw = json.loads(
            payload,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except VisualInterpretationParseError:
        raise
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise VisualInterpretationParseError(
            "VisualInterpretation 输出不是合法 JSON。"
        ) from exc
    if not isinstance(raw, dict):
        raise VisualInterpretationParseError(
            "VisualInterpretation 必须是 JSON object。"
        )
    try:
        return VisualInterpretationV2.model_validate_json(
            json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
            strict=True,
        )
    except ValidationError as exc:
        raise VisualInterpretationParseError(
            "VisualInterpretation 不符合 visual_interpretation_v2_1 Schema。"
        ) from exc


__all__ = [
    "MAX_VISUAL_INTERPRETATION_BYTES",
    "VisualInterpretationParseError",
    "parse_visual_interpretation_v2",
]
